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
from maina.core.registry import ArgSpec, InstallSpec, Manifest, OutputSpec, Registry
from maina.core.result import ToolResult
from maina.runner import ToolRunner
from maina.runtime import Runtime, isolated, register, runtime, use
from maina.tool import Tool

try:
    __version__ = _version("maina")
except _PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = [
    "AbstractToolError",
    "ArgError",
    "ArgSpec",
    "FunctionParser",
    "InstallContext",
    "InstallError",
    "InstallSpec",
    "InstallerBackend",
    "LanguageRuntimeProvider",
    "MainaError",
    "Manifest",
    "ManifestError",
    "OutputSpec",
    "ParseError",
    "Parser",
    "RegisterError",
    "Registry",
    "Runtime",
    "RuntimeProvisionError",
    "Tool",
    "ToolExecError",
    "ToolExitError",
    "ToolResult",
    "ToolRunner",
    "ToolTimeoutError",
    "UnsupportedPlatformError",
    "__version__",
    "isolated",
    "parser",
    "register",
    "runtime",
    "use",
]
