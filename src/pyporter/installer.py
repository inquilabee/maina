"""Install and resolve tool executables into the pyporter cache."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from pyporter.core.errors import InstallError
from pyporter.core.protocols import InstallContext, InstallerBackend, LanguageRuntimeProvider
from pyporter.mise import MiseRuntime

if TYPE_CHECKING:
    from pathlib import Path

    from pyporter.core.registry import Manifest, Registry, RequiresSpec


class Installer:
    """Dispatcher over registered :class:`InstallerBackend` implementations."""

    def __init__(
        self,
        *,
        cache_root: Path,
        registry: Registry,
        language: LanguageRuntimeProvider | None = None,
        backends: Mapping[str, InstallerBackend] | Sequence[InstallerBackend] | None = None,
    ) -> None:
        self._language = language
        self.cache_root = cache_root
        self.registry = registry
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

    @staticmethod
    @contextmanager
    def install_dir_lock(dest: Path) -> Iterator[None]:
        """Exclusive lock for a cache install directory (parallel prepare safety)."""
        dest.mkdir(parents=True, exist_ok=True)
        lock_path = dest / ".porter-install.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

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
        backend_name = manifest.install.backend
        version = manifest.version or "default"
        # Copy at the resolve boundary so backends cannot mutate a shared caller dict.
        env = dict(self._compose_requires_env(manifest.requires))
        backend = self._backends.get(backend_name)
        if backend is None:
            raise InstallError(f"unknown install backend: {backend_name!r}")
        ctx = InstallContext(cache_root=self.cache_root, language=self.language)
        lock_dir = self.cache_root / "tools" / manifest.cli / version
        with self.install_dir_lock(lock_dir):
            return backend.resolve(manifest, version=version, env=env, ctx=ctx)

    def _compose_requires_env(self, requires: RequiresSpec) -> dict[str, str]:
        """Ensure every pinned runtime and compose PATH without clobbering.

        ``LanguageRuntimeProvider.env`` typically copies ``os.environ`` per call,
        so invoking it once per pin would drop earlier bins. Mirror
        :meth:`MiseRuntime.env`: ``ensure`` each runtime, prepend each bin dir,
        set ``MISE_DATA_DIR`` once when any pin is present.
        """
        env = os.environ.copy()
        pins = requires.runtimes
        if not pins:
            return env
        for runtime, version in pins.items():
            bin_dir = self.language.ensure(runtime, version)
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["MISE_DATA_DIR"] = str(self.cache_root / "mise-data")
        return env

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


# Back-compat alias for tests/callers that import the module-level lock helper.
install_dir_lock = Installer.install_dir_lock
