"""pyporter errors."""

from __future__ import annotations


class PorterError(Exception):
    """Base error for pyporter."""


class ManifestError(PorterError):
    """Invalid or missing registry manifest."""


class AbstractToolError(PorterError):
    """Attempted to run an abstract (inheritance-only) tool surface."""


class ArgError(PorterError):
    """Invalid kwargs for a tool surface."""


class InstallError(PorterError):
    """Tool or runtime install/resolve failed."""


class ToolExitError(PorterError):
    """CLI exited with a non-success code."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int,
        tool: str,
        command: list[str],
        raw: str = "",
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.tool = tool
        self.command = command
        self.raw = raw
        self.stderr = stderr
