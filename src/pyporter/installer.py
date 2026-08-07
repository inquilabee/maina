"""Install and resolve tool executables into the pyporter cache."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pyporter.errors import InstallError
from pyporter.mise import MiseRuntime
from pyporter.protocols import InstallContext, InstallerBackend, LanguageRuntimeProvider
from pyporter.registry import Registry

if TYPE_CHECKING:
    from pyporter.registry import Manifest


class Installer:
    """Dispatcher over registered :class:`InstallerBackend` implementations."""

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        language: LanguageRuntimeProvider | None = None,
        registry: Registry | None = None,
        backends: Mapping[str, InstallerBackend] | Sequence[InstallerBackend] | None = None,
    ) -> None:
        self._language = language
        self.cache_root = cache_root or Path.home() / ".cache" / "pyporter"
        if cache_root is None and language is not None:
            cached = getattr(language, "cache_root", None)
            if isinstance(cached, Path):
                self.cache_root = cached
        self.registry = registry if registry is not None else Registry.default()
        self._backends: dict[str, InstallerBackend] = {}
        if backends is not None:
            self.register_backends(backends)

    @property
    def language(self) -> LanguageRuntimeProvider:
        if self._language is None:
            self._language = MiseRuntime(cache_root=self.cache_root)
        return self._language

    @language.setter
    def language(self, value: LanguageRuntimeProvider) -> None:
        self._language = value

    def register_backends(
        self,
        backends: Mapping[str, InstallerBackend] | Sequence[InstallerBackend],
    ) -> None:
        """Register backends; later entries replace the same ``name``."""
        items = cast(
            "list[InstallerBackend]",
            list(backends.values()) if isinstance(backends, Mapping) else list(backends),
        )
        for backend in items:
            self._backends[backend.name] = backend

    def resolve(self, manifest: Manifest) -> tuple[str, dict[str, str]]:
        """Resolve argv[0] path/name and process env for a manifest."""
        backend_name = str(manifest.install.get("backend", "path"))
        version = manifest.version or "default"
        env = (
            self.language.env(str(runtime), str(manifest.requires.get("version", "lts")))
            if (runtime := manifest.requires.get("runtime"))
            else os.environ.copy()
        )
        backend = self._backends.get(backend_name)
        if backend is None:
            raise InstallError(f"unknown install backend: {backend_name!r}")
        ctx = InstallContext(cache_root=self.cache_root, language=self.language)
        return backend.resolve(manifest, version=version, env=env, ctx=ctx)

    def prepare(self, manifest: Manifest) -> None:
        """Ensure runtimes and tool installs for one manifest."""
        if not manifest.abstract:
            self.resolve(manifest)

    def prepare_ids(self, tool_ids: list[str] | None = None) -> list[str]:
        """Prepare installs for given ids or all non-abstract manifests."""
        ids = tool_ids or self.registry.iter_ids()
        prepared: list[str] = []
        for tid in ids:
            manifest = self.registry.load(tid)
            if manifest.abstract:
                continue
            self.prepare(manifest)
            prepared.append(tid)
        return prepared
