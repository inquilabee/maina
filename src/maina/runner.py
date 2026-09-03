"""Subprocess runner for resolved tools."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from maina.core.argparser import ArgParser
from maina.core.envfilter import ESSENTIAL_ENV, filter_env
from maina.core.errors import ToolTimeoutError
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
        exe, env = self.installer.resolve(self.manifest, env_allow=env_allow)
        # resolve() already trimmed the base env for install subprocesses; trim
        # again here (backends may have re-read os.environ) and overlay the
        # manifest's own vars (empty dict when the manifest declares none).
        env = {**filter_env(env, env_allow), **self.manifest.env}
        argv = ArgParser(self.manifest.args).build(
            exe,
            self.manifest.subcommands,
            kwargs,
            extra=extra,
        )
        resolved_timeout = self._resolve_timeout(timeout)
        started = time.perf_counter()
        run = self._executor or subprocess.run
        try:
            proc = run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                cwd=cwd,
                timeout=resolved_timeout,
                input=stdin,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolTimeoutError(
                f"{self.manifest.id} timed out after {resolved_timeout}s",
                tool=self.manifest.id,
                timeout=float(resolved_timeout or 0.0),
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
        resolver = self.parsers.bind(
            parser_ref=self.manifest.parser,
            parser_path=self.manifest.parser_path,
            override=parser,
        )
        return resolver.apply(result)

    def _resolve_timeout(self, timeout: float | None) -> float | None:
        """Prefer explicit kwarg, then top-level manifest ``timeout``."""
        if timeout is not None:
            return float(timeout)
        raw = self.manifest.data.get("timeout")
        return None if raw is None else float(raw)
