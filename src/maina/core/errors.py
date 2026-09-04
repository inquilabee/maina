"""maina errors."""

from __future__ import annotations


class MainaError(Exception):
    """Base error for maina."""


class ManifestError(MainaError):
    """Invalid or missing registry manifest.

    ``files`` records the manifest file(s) the message came from, leaf first.
    A tool id is derived from a path and says nothing about which file in an
    ``extends`` chain is actually wrong — one typo in a shared ``base.yaml``
    would otherwise report once per child and name none of them.
    """

    def __init__(self, message: str, *, files: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.files = files


class AbstractToolError(MainaError):
    """Attempted to run an abstract (inheritance-only) tool surface."""


class ArgError(MainaError):
    """Invalid kwargs for a tool surface."""


class ParseError(MainaError):
    """A configured parser could not turn tool output into structured data."""


class ToolExecError(MainaError):
    """The tool subprocess could not be started (bad cwd, missing exe, ...)."""

    def __init__(self, message: str, *, tool: str, command: list[str]) -> None:
        super().__init__(message)
        self.tool = tool
        self.command = command


class InstallError(MainaError):
    """Tool or runtime install/resolve failed."""


class RuntimeProvisionError(InstallError):
    """A language runtime (mise-managed Node/Python/...) could not be provisioned.

    Subclass of :class:`InstallError` so ``except InstallError`` covers a runtime
    bootstrap failure too; callers that care can catch this specifically.
    """


class UnsupportedPlatformError(InstallError):
    """The host platform is not in a manifest's declared ``platforms`` list."""


class ToolTimeoutError(MainaError):
    """A tool subprocess exceeded its resolved timeout.

    This is a *run*-phase failure (not an install failure); it carries the tool
    id, timeout, and argv so callers can report or retry.
    """

    def __init__(
        self,
        message: str,
        *,
        tool: str,
        timeout: float,
        command: list[str],
    ) -> None:
        super().__init__(message)
        self.tool = tool
        self.timeout = timeout
        self.command = command


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
