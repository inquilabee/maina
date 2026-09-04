"""Subprocess runner for resolved tools."""

from __future__ import annotations

import string
import subprocess
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maina.core.argparser import ArgParser
from maina.core.envfilter import ESSENTIAL_ENV, filter_env
from maina.core.errors import ArgError, ToolExecError, ToolTimeoutError
from maina.core.parsers import ParserResolver
from maina.core.result import ToolResult

if TYPE_CHECKING:
    from maina.core.registry import Manifest
    from maina.installer import Installer

# Test-only injectable; not a public pluggable runner API.
Executor = Callable[..., subprocess.CompletedProcess[Any]]

# Cap on an ``output.from_file`` report slurped into memory — every other input
# path in maina is bounded (download / extract caps); this is the last one.
REPORT_MAX_BYTES = 64 * 1024 * 1024

__all__ = ["ESSENTIAL_ENV", "Executor", "ToolRunner"]


class ToolRunner:
    """Instance-scoped owner of install → argv → subprocess → parse."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        installer: Installer,
        parsers: ParserResolver | None = None,
        executor: Executor | None = None,
    ) -> None:
        self.manifest = manifest
        self.installer = installer
        self.parsers = parsers or ParserResolver()
        self._executor = executor

    def run(
        self,
        kwargs: dict[str, Any],
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env_allow: Iterable[str] | None = None,
        env: dict[str, str] | None = None,
        stdin: str | bytes | None = None,
        text: bool = True,
    ) -> ToolResult:
        """Install/resolve, build argv, execute, and parse.

        ``env_allow`` restricts which of the caller's environment variables reach
        the tool subprocess. When ``None`` (default) the full composed environment
        is forwarded; when set, only :data:`ESSENTIAL_ENV` plus the named keys are
        passed, so secrets in ``os.environ`` stay out of third-party binaries.
        ``env`` overlays extra ``NAME: value`` pairs for this call (after the
        trim, before the manifest's own ``env`` block); the manifest block still
        wins a key collision. ``stdin`` is written to the tool's stdin. With
        ``text=False`` ``result.raw`` / ``result.stderr`` are ``bytes`` and
        neither the parser nor ``output.from_file`` runs — for a tool that emits
        an archive, image, or other binary on stdout.
        """
        # Materialise env_allow once — it is consumed twice (install-phase and
        # tool subprocess), so a one-shot generator would apply differently.
        env_allow = None if env_allow is None else frozenset(env_allow)
        # Build argv first so a bad kwarg raises ArgError *before* any install
        # work (a typo'd argument should not pay for a download).
        argv = ArgParser(self.manifest.args, tool_id=self.manifest.id).build(
            self.manifest.cli,
            self.manifest.subcommands,
            kwargs,
            extra=extra,
            separator=self.manifest.separator,
        )
        exe, composed_env = self.installer.resolve(self.manifest, env_allow=env_allow)
        # resolve() already trimmed the base env for install subprocesses; trim
        # again here (backends may have re-read os.environ), then overlay the
        # caller's per-run ``env`` and finally the manifest's own vars (which
        # win a collision, and are an empty dict when the manifest declares none).
        proc_env = {**filter_env(composed_env, env_allow), **(env or {}), **self.manifest.env}
        argv[0] = exe
        resolved_timeout = self._resolve_timeout(timeout)
        started = time.perf_counter()
        started_wall = time.time()
        proc = self._execute(
            argv, env=proc_env, cwd=cwd, timeout=resolved_timeout, stdin=stdin, text=text
        )
        duration_ms = (time.perf_counter() - started) * 1000
        result = self._build_result(proc, argv=argv, cwd=cwd, exe=exe, duration_ms=duration_ms)
        if not text:
            # Binary passthrough: no text parser, no report-file interpolation.
            return result
        self._apply_from_file(result, kwargs, cwd, started_wall)
        if result.parse_error is not None:
            # ``from_file`` already failed; don't run the parser over stdout and
            # clobber the diagnostic.
            return result
        resolver = self.parsers.bind(
            parser_ref=self.manifest.parser,
            parser_path=self.manifest.parser_path,
            override=parser,
        )
        return resolver.apply(result)

    def _build_result(
        self,
        proc: subprocess.CompletedProcess[Any],
        *,
        argv: list[str],
        cwd: str | None,
        exe: str,
        duration_ms: float,
    ) -> ToolResult:
        """Wrap a finished process in a ``ToolResult`` (bytes stdout when ``text=False``)."""
        binary = isinstance(proc.stdout, bytes) or isinstance(proc.stderr, bytes)
        raw_bytes = proc.stdout if isinstance(proc.stdout, bytes) else None
        stderr_bytes = proc.stderr if isinstance(proc.stderr, bytes) else None
        return ToolResult(
            tool=self.manifest.id,
            ok=proc.returncode in set(self.manifest.output.success_codes),
            exit_code=proc.returncode,
            raw=self._as_text(proc.stdout),
            stderr=self._as_text(proc.stderr),
            command=argv,
            cwd=cwd,
            exe=exe,
            version=self.manifest.version,
            duration_ms=duration_ms,
            raw_bytes=raw_bytes if binary else None,
            stderr_bytes=stderr_bytes if binary else None,
        )

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        """stdout/stderr as text — decode lossily when the run was ``text=False``."""
        return value.decode("utf-8", "replace") if isinstance(value, bytes) else value or ""

    @staticmethod
    def _coerce_stdin(stdin: str | bytes, *, text: bool) -> str | bytes:
        """Match ``stdin`` to the run's text/binary mode (str under text, bytes otherwise)."""
        as_bytes = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        as_text = stdin.decode("utf-8", "replace") if isinstance(stdin, bytes) else stdin
        return as_text if text else as_bytes

    def _execute(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: str | None,
        timeout: float | None,
        stdin: str | bytes | None,
        text: bool,
    ) -> subprocess.CompletedProcess[Any]:
        """Run the subprocess, mapping a timeout / spawn failure to a MainaError."""
        run = self._executor or subprocess.run
        run_kwargs: dict[str, Any] = {
            "check": False,
            "capture_output": True,
            "text": text,
            "env": env,
            "cwd": cwd,
            "timeout": timeout,
        }
        if text:
            run_kwargs |= {"encoding": "utf-8", "errors": "replace"}
        # Default to no stdin so a tool that reads stdin does not block on the
        # caller's terminal; opt in with stdin=.
        if stdin is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = self._coerce_stdin(stdin, text=text)
        try:
            return run(argv, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise ToolTimeoutError(
                f"{self.manifest.id} timed out after {timeout}s",
                tool=self.manifest.id,
                timeout=float(timeout or 0.0),
                command=argv,
            ) from exc
        except OSError as exc:
            raise ToolExecError(
                f"{self.manifest.id}: could not run {argv[0]}: {exc}",
                tool=self.manifest.id,
                command=argv,
            ) from exc

    def _apply_from_file(
        self, result: ToolResult, kwargs: dict[str, Any], cwd: str | None, started_wall: float
    ) -> None:
        """Point the parser at ``output.from_file`` instead of stdout.

        ``result.raw`` is left as the tool's stdout; the file contents go on
        ``result.report``. Every failure (unresolved template, escape, missing
        file, a report older than this run) is surfaced on ``result.parse_error``
        — never a silent fallback.
        """
        template = self.manifest.output.from_file
        if not template:
            return
        base = (Path(cwd) if cwd else Path.cwd()).resolve()
        path, error = self._resolve_report_path(template, base, kwargs)
        if path is None:
            result.parse_error = error
            return
        if not path.is_file():
            result.parse_error = f"output.from_file not found: {path}"
            result.meta["parsed_from_missing"] = str(path)
            return
        stat = path.stat()
        result.meta |= {"parsed_from": str(path), "parsed_from_mtime": stat.st_mtime}
        if stat.st_mtime < started_wall - 2.0:
            result.parse_error = f"output.from_file {path} was not written by this run (stale)"
            return
        if stat.st_size > REPORT_MAX_BYTES:
            result.parse_error = (
                f"output.from_file {path} is {stat.st_size} bytes, over the {REPORT_MAX_BYTES} cap"
            )
            return
        result.report = path.read_text(encoding="utf-8", errors="replace")

    def _resolve_report_path(
        self, template: str, base: Path, kwargs: dict[str, Any]
    ) -> tuple[Path | None, str | None]:
        """Interpolate ``output.from_file`` to a path under *base* (``(path, error)``)."""
        resolved = ArgParser(self.manifest.args, tool_id=self.manifest.id).resolved_values(kwargs)
        subs = {
            key: value
            for key, value in resolved.items()
            if key != "cwd" and isinstance(value, (str, int, float)) and not isinstance(value, bool)
        }
        try:
            self._check_from_file_template(template)
            rel = template.format(cwd=str(base), **subs)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            return None, f"output.from_file template {template!r} unresolved: {exc}"
        path = (base / rel).resolve()
        return (
            (path, None)
            if path.is_relative_to(base)
            else (None, f"output.from_file escapes the run directory: {path}")
        )

    @staticmethod
    def _check_from_file_template(template: str) -> None:
        """Reject an ``output.from_file`` placeholder that reaches past a bare name.

        ``{cwd}`` and ``{argname}`` are fine; ``{cwd.__class__…}`` / ``{a[0]}``
        are not — the template only names a directory and args. A conversion or
        format spec (``{out!r}``, ``{out:>1000000000}``) is rejected too: it can
        drive a huge allocation inside ``str.format`` and buys nothing here.
        """
        for _literal, field, spec, conv in string.Formatter().parse(template):
            if field is None:
                continue
            if set("[.") & set(field):
                raise ValueError(f"placeholder {field!r} may not use attribute or item access")
            if spec or conv:
                raise ValueError(f"placeholder {field!r} may not carry a conversion or format spec")

    def _resolve_timeout(self, timeout: float | None) -> float | None:
        """Prefer explicit kwarg, then top-level manifest ``timeout``.

        A non-positive value (``0`` / negative) means "no limit", not "expire
        immediately" — the manifest loader already rejects ``timeout <= 0``.
        """
        raw = timeout if timeout is not None else self.manifest.data.get("timeout")
        if raw is None:
            return None
        try:
            seconds = float(raw)
        except (TypeError, ValueError) as exc:
            raise ArgError(f"timeout must be a number of seconds, got {raw!r}") from exc
        return seconds if seconds > 0 else None
