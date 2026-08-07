"""Subprocess runner for resolved tools."""

from __future__ import annotations

import subprocess
import time
from typing import TYPE_CHECKING, Any

from pyporter.argparser import ArgParser
from pyporter.installer import Installer
from pyporter.parsers import ParserResolver
from pyporter.result import ToolResult

if TYPE_CHECKING:
    from pyporter.registry import Manifest


class ToolRunner:
    """Instance-scoped owner of install → argv → subprocess → parse."""

    def __init__(
        self,
        manifest: Manifest,
        *,
        installer: Installer | None = None,
        parsers: ParserResolver | None = None,
    ) -> None:
        self.manifest = manifest
        self.installer = installer or Installer()
        self.parsers = parsers or ParserResolver()

    def run(
        self,
        kwargs: dict[str, Any],
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
    ) -> ToolResult:
        """Install/resolve, build argv, execute, and parse."""
        exe, env = self.installer.resolve(self.manifest)
        argv = ArgParser(self.manifest.args).build(
            exe,
            self.manifest.subcommands,
            kwargs,
            extra=extra,
        )
        started = time.perf_counter()
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd,
        )
        duration_ms = (time.perf_counter() - started) * 1000
        success_codes = self.manifest.output.get("success_codes", [0])
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
