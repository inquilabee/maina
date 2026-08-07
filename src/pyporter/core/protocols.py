"""Pluggable contracts for installers, language runtimes, and parsers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from pyporter.core.registry import Manifest
    from pyporter.core.result import ToolResult


@runtime_checkable
class Parser(Protocol):
    """Transform tool stdout into structured ``ToolResult.parsed`` data.

    Plain callables (module-level ``parse``, lambdas, bound methods) satisfy this
    protocol. Prefer wrapping via :func:`pyporter.core.parsers.parser` /
    :class:`~pyporter.core.parsers.FunctionParser` when registering.
    """

    def __call__(self, raw: str, result: ToolResult) -> Any:
        """Parse *raw* stdout; *result* is the run outcome for context."""
        ...


@runtime_checkable
class LanguageRuntimeProvider(Protocol):
    """Install and resolve language runtimes (e.g. mise-backed Node)."""

    def ensure(self, runtime: str, version: str) -> Path:
        """Ensure a language runtime is installed; return its bin directory."""
        ...

    def env(self, runtime: str, version: str) -> dict[str, str]:
        """Environment with the language runtime bin prepended to PATH."""
        ...


@dataclass(slots=True)
class InstallContext:
    """Thin resolve context for installer backends (not the composition Runtime)."""

    cache_root: Path
    language: LanguageRuntimeProvider

    def tool_bin_dir(self, name: str, version: str = "default") -> Path:
        """Per-tool versioned bin directory under the cache."""
        path = self.cache_root / "tools" / name / version / "bin"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def prepend_path(env: dict[str, str], directory: Path | str) -> dict[str, str]:
        """Prepend *directory* to ``PATH`` in *env* and return *env*."""
        env["PATH"] = f"{directory}{os.pathsep}{env.get('PATH', '')}"
        return env


@runtime_checkable
class InstallerBackend(Protocol):
    """One install.backend name in manifests."""

    @property
    def name(self) -> str:
        """Dispatch key matching ``install.backend`` in YAML."""
        ...

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        """Return ``(executable, env)``. May mutate env (e.g. PATH)."""
        ...
