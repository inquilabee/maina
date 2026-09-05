"""Subprocess runner for resolved tools."""

from __future__ import annotations

import string
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maina.core.argparser import ArgParser
from maina.core.envfilter import ESSENTIAL_ENV, filter_env
from maina.core.errors import ArgError, ToolExecError, ToolOutputError, ToolTimeoutError
from maina.core.parsers import ParserResolver
from maina.core.result import ToolResult
from maina.core.subprocess_utils import OutputCapExceededError, run_capped

if TYPE_CHECKING:
    from maina.core.registry import Manifest
    from maina.installer import Installer

# Test-only injectable; not a public pluggable runner API.
Executor = Callable[..., subprocess.CompletedProcess[Any]]

# Cap on an ``output.from_file`` report slurped into memory (download / extract
# have their own caps).
REPORT_MAX_BYTES = 64 * 1024 * 1024
# Cap on stdout/stderr collected from the tool subprocess itself — a tool that
# floods output is killed and raises ToolOutputError instead of buffering
# unbounded into this process (see ToolRunner._run_capped).
RUN_OUTPUT_MAX_BYTES = 64 * 1024 * 1024
DRAIN_CHUNK_SIZE = 64 * 1024

__all__ = ["ESSENTIAL_ENV", "Executor", "ToolRunner"]


class ToolRunner:
    """Instance-scoped owner of install → argv → subprocess → parse."""

    # How far an ``output.from_file`` report's mtime may predate the run that
    # should have written it before it is rejected as a leftover from a previous
    # run. Slack, not precision: filesystem mtime granularity and clock offsets
    # (NFS, some container bind mounts) make an exact comparison unusable.
    # A manifest whose tool legitimately leaves a report alone sets
    # ``output.allow_stale_report: true``.
    REPORT_MTIME_SLACK = 2.0

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
        ``text=False``, ``result.raw_bytes`` / ``result.stderr_bytes`` hold the
        raw bytes (``result.raw`` / ``result.stderr`` stay lossy-decoded text
        views) and neither the parser nor ``output.from_file`` runs — for a
        tool that emits an archive, image, or other binary on stdout.
        """
        self._check_env(env)
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

    def _check_env(self, env: Any) -> None:
        """``env=`` must be a mapping of ``str`` to ``str``.

        ``subprocess`` rejects anything else with a bare ``TypeError`` raised
        from inside ``Popen`` — not a ``MainaError``, and with no mention of
        which variable. A port number or a timeout as an env value is a natural
        mistake; say so here instead.
        """
        if env is None:
            return
        if not isinstance(env, Mapping):
            raise ArgError(
                f"{self.manifest.id}: env= must be a mapping of NAME: value, not "
                f"{type(env).__name__} (a manifest arg named env belongs in args={{'env': ...}})"
            )
        for key, value in env.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ArgError(
                    f"{self.manifest.id}: env[{key!r}] must be a string name and value, not "
                    f"{type(key).__name__} -> {type(value).__name__} "
                    "(the environment is text; stringify it at the call site)"
                )

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
        """Run the subprocess, mapping a timeout / spawn failure to a MainaError.

        The real (non test-injected) path bounds stdout/stderr collection via
        :meth:`_run_capped`; an injected ``executor`` (test-only) keeps calling
        ``subprocess.run``'s own ``capture_output=True`` contract unchanged.
        """
        if self._executor is not None:
            return self._execute_via(
                self._executor, argv, env=env, cwd=cwd, timeout=timeout, stdin=stdin, text=text
            )
        try:
            return self._run_capped(argv, env=env, cwd=cwd, timeout=timeout, stdin=stdin, text=text)
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

    def _execute_via(
        self,
        run: Executor,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: str | None,
        timeout: float | None,
        stdin: str | bytes | None,
        text: bool,
    ) -> subprocess.CompletedProcess[Any]:
        """The pre-cap ``subprocess.run``-shaped call, used only for the test
        ``executor=`` injection point — real execution goes through
        :meth:`_run_capped` instead."""
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

    def _run_capped(
        self,
        argv: list[str],
        *,
        env: dict[str, str],
        cwd: str | None,
        timeout: float | None,
        stdin: str | bytes | None,
        text: bool,
    ) -> subprocess.CompletedProcess[Any]:
        """Like ``subprocess.run(capture_output=True)``, but stdout/stderr are
        each bounded at :data:`RUN_OUTPUT_MAX_BYTES` (see
        :func:`~maina.core.subprocess_utils.run_capped`): a tool that floods
        output used to buffer unbounded into this process (``subprocess.run``
        reads to EOF with no size limit) — silently truncating and returning
        a partial result as if the run had finished is exactly the class of
        silent-wrong-result this codebase treats as a defect elsewhere, so
        crossing the cap kills the process and raises :class:`ToolOutputError`
        instead.
        """
        stdin_bytes = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        try:
            completed = run_capped(
                argv,
                env=env,
                cwd=cwd,
                timeout=timeout,
                stdin=stdin_bytes,
                max_bytes=RUN_OUTPUT_MAX_BYTES,
                chunk_size=DRAIN_CHUNK_SIZE,
            )
        except OutputCapExceededError as exc:
            raise ToolOutputError(
                f"{self.manifest.id}: stdout/stderr exceeded the {exc.max_bytes} byte cap",
                tool=self.manifest.id,
                command=argv,
            ) from exc
        out_buf, err_buf = bytearray(completed.stdout), bytearray(completed.stderr)
        return self._completed_process(argv, completed.returncode, out_buf, err_buf, text=text)

    @staticmethod
    def _completed_process(
        argv: list[str], returncode: int, out_buf: bytearray, err_buf: bytearray, *, text: bool
    ) -> subprocess.CompletedProcess[Any]:
        stdout: Any = bytes(out_buf)
        stderr: Any = bytes(err_buf)
        if text:
            stdout = stdout.decode("utf-8", "replace")
            stderr = stderr.decode("utf-8", "replace")
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

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
        stale = stat.st_mtime < started_wall - self.REPORT_MTIME_SLACK
        if stale and not self.manifest.output.allow_stale_report:
            result.parse_error = (
                f"output.from_file {path} was not written by this run (stale) — set "
                "output.allow_stale_report: true if the tool leaves an unchanged report alone"
            )
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
