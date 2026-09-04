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
Executor = Callable[..., subprocess.CompletedProcess[str]]

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
        stdin: str | None = None,
    ) -> ToolResult:
        """Install/resolve, build argv, execute, and parse.

        ``env_allow`` restricts which of the caller's environment variables reach
        the tool subprocess. When ``None`` (default) the full composed environment
        is forwarded; when set, only :data:`ESSENTIAL_ENV` plus the named keys are
        passed, so secrets in ``os.environ`` stay out of third-party binaries.
        The manifest's own ``env`` block is layered on afterwards and always
        forwarded. ``stdin`` is written to the tool's stdin.
        """
        # Build argv first so a bad kwarg raises ArgError *before* any install
        # work (a typo'd argument should not pay for a download).
        argv = ArgParser(self.manifest.args).build(
            self.manifest.cli,
            self.manifest.subcommands,
            kwargs,
            extra=extra,
        )
        exe, env = self.installer.resolve(self.manifest, env_allow=env_allow)
        # resolve() already trimmed the base env for install subprocesses; trim
        # again here (backends may have re-read os.environ) and overlay the
        # manifest's own vars (empty dict when the manifest declares none).
        env = {**filter_env(env, env_allow), **self.manifest.env}
        argv[0] = exe
        resolved_timeout = self._resolve_timeout(timeout)
        started = time.perf_counter()
        run = self._executor or subprocess.run
        run_kwargs: dict[str, Any] = {
            "check": False,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": env,
            "cwd": cwd,
            "timeout": resolved_timeout,
        }
        # Default to no stdin so a tool that reads stdin does not block on the
        # caller's terminal; opt in with stdin=.
        if stdin is None:
            run_kwargs["stdin"] = subprocess.DEVNULL
        else:
            run_kwargs["input"] = stdin
        try:
            proc = run(argv, **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            raise ToolTimeoutError(
                f"{self.manifest.id} timed out after {resolved_timeout}s",
                tool=self.manifest.id,
                timeout=float(resolved_timeout or 0.0),
                command=argv,
            ) from exc
        except OSError as exc:
            raise ToolExecError(
                f"{self.manifest.id}: could not run {exe}: {exc}",
                tool=self.manifest.id,
                command=argv,
            ) from exc
        duration_ms = (time.perf_counter() - started) * 1000
        success_codes = self.manifest.output.success_codes
        ok = proc.returncode in set(success_codes)
        result = ToolResult(
            tool=self.manifest.id,
            ok=ok,
            exit_code=proc.returncode,
            raw=proc.stdout or "",
            stderr=proc.stderr or "",
            command=argv,
            cwd=cwd,
            exe=exe,
            version=self.manifest.version,
            duration_ms=duration_ms,
        )
        self._apply_from_file(result, kwargs, cwd)
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

    def _apply_from_file(self, result: ToolResult, kwargs: dict[str, Any], cwd: str | None) -> None:
        """Point the parser at ``output.from_file`` instead of stdout.

        ``result.raw`` is left as the tool's stdout; the file contents go on
        ``result.report``. Every failure (unresolved template, escape, missing
        file) is surfaced on ``result.parse_error`` — never a silent fallback.
        """
        template = self.manifest.output.from_file
        if not template:
            return
        base = (Path(cwd) if cwd else Path.cwd()).resolve()
        subs = {
            key: value
            for key, value in ArgParser(self.manifest.args).resolved_values(kwargs).items()
            if isinstance(value, (str, int, float)) and not isinstance(value, bool)
        }
        try:
            self._check_from_file_template(template)
            rel = template.format(cwd=str(base), **subs)
        except (KeyError, IndexError, ValueError) as exc:
            result.parse_error = f"output.from_file template {template!r} unresolved: {exc}"
            return
        path = (base / rel).resolve()
        if not path.is_relative_to(base):
            result.parse_error = f"output.from_file escapes the run directory: {path}"
            return
        if not path.is_file():
            result.parse_error = f"output.from_file not found: {path}"
            result.meta["parsed_from_missing"] = str(path)
            return
        result.report = path.read_text(encoding="utf-8", errors="replace")
        result.meta["parsed_from"] = str(path)

    @staticmethod
    def _check_from_file_template(template: str) -> None:
        """Reject an ``output.from_file`` placeholder that reaches past a bare name.

        ``{cwd}`` and ``{argname}`` are fine; ``{cwd.__class__…}`` / ``{a[0]}``
        are not — the template only names a directory and args.
        """
        for _literal, field, _spec, _conv in string.Formatter().parse(template):
            if field and set("[.") & set(field):
                raise ValueError(f"placeholder {field!r} may not use attribute or item access")

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
