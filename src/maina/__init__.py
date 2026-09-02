"""Call non-Python CLIs from Python via a registry-first Tool runtime."""

from __future__ import annotations

from maina.core.errors import (
    InstallError,
    MainaError,
    ManifestError,
    RegisterError,
    RuntimeProvisionError,
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

__all__ = [
    "FunctionParser",
    "InstallContext",
    "InstallError",
    "InstallerBackend",
    "LanguageRuntimeProvider",
    "MainaError",
    "ManifestError",
    "Parser",
    "RegisterError",
    "Runtime",
    "RuntimeProvisionError",
    "Tool",
    "ToolExitError",
    "ToolResult",
    "ToolTimeoutError",
    "UnsupportedPlatformError",
    "isolated",
    "parser",
    "register",
    "runtime",
    "use",
]
