"""Structured result from a tool run."""

from __future__ import annotations

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
    version: str | None = None
    duration_ms: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

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
