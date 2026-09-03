"""Structured result from a tool run."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from maina.core.errors import ToolExitError


@dataclass(slots=True)
class ToolResult:
    """Outcome of running a registry tool."""

    tool: str
    ok: bool
    exit_code: int
    raw: str
    stderr: str
    command: list[str]
    parsed: Any = None
    parse_error: str | None = None
    report: str | None = None
    cwd: str | None = None
    exe: str | None = None
    version: str | None = None
    duration_ms: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def parse_input(self) -> str:
        """What the parser sees: the ``output.from_file`` report, else stdout."""
        return self.report if self.report is not None else self.raw

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view with a stable key set.

        A non-serialisable ``parsed`` is dropped and noted in ``parse_error``.
        """
        parsed = self.parsed
        parse_error = self.parse_error
        try:
            json.dumps(parsed)
        except (TypeError, ValueError):
            kind = type(parsed).__name__
            parse_error = parse_error or f"parsed value is not JSON-serialisable ({kind})"
            parsed = None
        return {
            "tool": self.tool,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "raw": self.raw,
            "stderr": self.stderr,
            "command": list(self.command),
            "parsed": parsed,
            "parse_error": parse_error,
            "report": self.report,
            "cwd": self.cwd,
            "exe": self.exe,
            "version": self.version,
            "duration_ms": self.duration_ms,
            "meta": dict(self.meta),
        }

    def raise_for_status(self) -> ToolResult:
        """Raise ToolExitError when the run was not ok."""
        if not self.ok:
            raise ToolExitError(
                f"{self.tool} exited with code {self.exit_code}",
                exit_code=self.exit_code,
                tool=self.tool,
                command=self.command,
                raw=self.raw,
                stderr=self.stderr,
            )
        return self
