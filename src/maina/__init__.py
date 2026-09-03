"""Call non-Python CLIs from Python via a registry-first Tool runtime."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _version

from maina.core.errors import (
    AbstractToolError,
    ArgError,
    InstallError,
    MainaError,
    ManifestError,
    ParseError,
    RegisterError,
    RuntimeProvisionError,
    ToolExecError,
    ToolExitError,
    ToolTimeoutError,
    UnsupportedPlatformError,
)
from maina.core.parsers import FunctionParser, parser
from maina.core.protocols import (
    InstallContext,
    InstallerBackend,
    LanguageRuntimeProvider,
    Parser,
)
from maina.core.result import ToolResult
from maina.runtime import Runtime, isolated, register, runtime, use
from maina.tool import Tool

try:
    __version__ = _version("maina")
except _PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = [
    "AbstractToolError",
    "ArgError",
    "FunctionParser",
    "InstallContext",
    "InstallError",
    "InstallerBackend",
    "LanguageRuntimeProvider",
    "MainaError",
    "ManifestError",
    "ParseError",
    "Parser",
    "RegisterError",
    "Runtime",
    "RuntimeProvisionError",
    "Tool",
    "ToolExecError",
    "ToolExitError",
    "ToolResult",
    "ToolTimeoutError",
    "UnsupportedPlatformError",
    "__version__",
    "isolated",
    "parser",
    "register",
    "runtime",
    "use",
]
