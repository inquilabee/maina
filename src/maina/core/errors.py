"""maina errors."""

from __future__ import annotations


class MainaError(Exception):
    """Base error for maina."""


class ManifestError(MainaError):
    """Invalid or missing registry manifest."""


class AbstractToolError(MainaError):
    """Attempted to run an abstract (inheritance-only) tool surface."""


class ArgError(MainaError):
    """Invalid kwargs for a tool surface."""


class InstallError(MainaError):
    """Tool or runtime install/resolve failed."""


class RegisterError(MainaError):
    """Invalid register path or composition registration failure."""


class ToolExitError(MainaError):
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
