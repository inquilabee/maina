"""Parse git remote add output (usually empty on success)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyporter.result import ToolResult


def parse(raw: str, result: ToolResult) -> Any:
    """Return a small status dict for git remote add."""
    return {"ok": result.ok, "raw": raw}
