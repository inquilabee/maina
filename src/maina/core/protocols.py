"""Pluggable contracts for installers, language runtimes, and parsers."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.registry import Manifest
    from maina.core.result import ToolResult


@runtime_checkable
class Parser(Protocol):
    """Transform tool stdout into structured ``ToolResult.parsed`` data.

    Plain callables (module-level ``parse``, lambdas, bound methods) satisfy this
    protocol. Prefer wrapping via :func:`maina.core.parsers.parser` /
    :class:`~maina.core.parsers.FunctionParser` when registering.
    """

    def __call__(self, raw: str, result: ToolResult) -> Any:
        """Parse *raw* stdout; *result* is the run outcome for context."""
        ...


@runtime_checkable
class LanguageRuntimeProvider(Protocol):
    """Install and resolve language runtimes (e.g. mise-backed Node)."""

    def ensure(self, runtime: str, version: str, *, base_env: dict[str, str] | None = None) -> Path:
        """Ensure a language runtime is installed; return its bin directory.

        ``base_env`` is the environment the provisioning subprocesses should run
        with. When ``None`` the provider falls back to the full ``os.environ``;
        callers that honour ``env_allow`` pass the already-trimmed environment so
        a secret never reaches ``mise`` / ``npm``.
        """
        ...

    def env(
        self, runtime: str, version: str, *, base_env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Environment with the language runtime bin prepended to PATH.

        ``base_env`` seeds the result (and the provisioning subprocesses) instead
        of ``os.environ`` when given.
        """
        ...


@dataclass(slots=True)
class InstallContext:
    """Thin resolve context for installer backends (not the composition Runtime)."""

    cache_root: Path
    language: LanguageRuntimeProvider
    probe_path: str | None = None
    """PATH a side-effect-free ``probe`` should search, with the bin dirs of the
    manifest's already-installed ``requires:`` runtimes prepended. ``None`` (the
    default, and what ``resolve`` gets) means the ambient ``PATH``.
    """

    def tool_bin_dir(self, name: str, version: str = "default") -> Path:
        """Per-tool versioned bin directory under the cache (pure — does not
        create it; see :meth:`ensure_tool_bin_dir` for that)."""
        return self.cache_root / "tools" / name / version / "bin"

    def ensure_tool_bin_dir(self, name: str, version: str = "default") -> Path:
        """:meth:`tool_bin_dir`, creating it (and parents) if missing."""
        path = self.tool_bin_dir(name, version)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def prepend_path(env: dict[str, str], directory: Path | str) -> dict[str, str]:
        """Prepend *directory* to ``PATH`` in *env* and return *env*."""
        env["PATH"] = f"{directory}{os.pathsep}{env.get('PATH', '')}"
        return env


class InstallerBackend(ABC):
    """One install.backend name in manifests.

    A real base class (not a ``Protocol``): backends declare conformance by
    explicit inheritance, and Python enforces it — a subclass that forgets
    ``resolve`` or ``name`` fails at instantiation with a clear
    ``TypeError``, not a confusing ``AttributeError`` the first time a
    manifest dispatches to it. ``probe`` stays a plain optional method
    (undeclared here): :class:`~maina.installer.Installer` treats a backend
    without one as always "not satisfied" rather than requiring every
    backend to implement a side-effect-free check.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Dispatch key matching ``install.backend`` in YAML."""

    @abstractmethod
    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        """Return ``(executable, env)``. May mutate env (e.g. PATH)."""
