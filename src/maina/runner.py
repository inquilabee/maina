"""Subprocess runner for resolved tools."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from maina.core.argparser import ArgParser
from maina.core.errors import InstallError
from maina.core.parsers import ParserResolver
from maina.core.result import ToolResult

if TYPE_CHECKING:
    from maina.core.registry import Manifest
    from maina.installer import Installer

# Test-only injectable; not a public pluggable runner API.
Executor = Callable[..., subprocess.CompletedProcess[str]]


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
    ) -> ToolResult:
        """Install/resolve, build argv, execute, and parse."""
        exe, env = self.installer.resolve(self.manifest)
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
                env=env,
                cwd=cwd,
                timeout=resolved_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallError(f"{self.manifest.id} timed out after {resolved_timeout}s") from exc
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
