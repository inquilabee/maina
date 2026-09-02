"""Install and resolve tool executables into the maina cache."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from maina.core.errors import InstallError, UnsupportedPlatformError
from maina.core.platform import current_platform
from maina.core.protocols import InstallContext, InstallerBackend, LanguageRuntimeProvider
from maina.mise import MiseRuntime

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.registry import Manifest, Registry, RequiresSpec


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
        lock_path = dest / ".maina-install.lock"
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

    @staticmethod
    def _require_supported_platform(manifest: Manifest) -> None:
        """Fail early when the host is not in the manifest's ``platforms`` list."""
        allowed = manifest.platforms
        host = current_platform()
        if allowed and host not in allowed:
            raise UnsupportedPlatformError(
                f"{manifest.id}: not supported on {host} (manifest platforms: {', '.join(allowed)})"
            )

    def resolve(self, manifest: Manifest) -> tuple[str, dict[str, str]]:
        """Resolve argv[0] path/name and process env for a manifest."""
        self._require_supported_platform(manifest)
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

    def is_satisfied(self, manifest: Manifest) -> bool:
        """True when *manifest* needs no install work on this host.

        Side-effect free: no download, no ``mise`` call. Abstract surfaces and
        tools unsupported on this host count as satisfied (nothing to do). A
        backend without a ``probe`` method is treated as not satisfied.
        """
        if manifest.abstract:
            return True
        if manifest.platforms and current_platform() not in manifest.platforms:
            return True
        backend = self._backends.get(manifest.install.backend)
        probe = getattr(backend, "probe", None)
        if probe is None:
            return False
        ctx = InstallContext(cache_root=self.cache_root, language=self.language)
        satisfied: bool = probe(manifest, version=manifest.version or "default", ctx=ctx)
        return satisfied

    ABSTRACT_PREPARE_HINT = "prepare a concrete child id or omit abstracts from prepare"

    @classmethod
    def _abstract_error(cls, tool_id: str) -> InstallError:
        return InstallError(
            f"cannot prepare abstract tool {tool_id!r}; {cls.ABSTRACT_PREPARE_HINT}"
        )

    def _selected_manifests(self, tool_ids: list[str] | None) -> Iterator[tuple[str, Manifest]]:
        """Yield ``(id, manifest)`` for concrete, host-supported tools.

        Explicit abstract ids raise :class:`InstallError`; when *tool_ids* is
        omitted, abstract surfaces and tools unsupported on this host are
        skipped.
        """
        explicit = tool_ids is not None
        ids = tool_ids if explicit else self.registry.iter_ids()
        for tid in ids:
            manifest = self.registry.load(tid)
            if manifest.abstract:
                if explicit:
                    raise self._abstract_error(tid)
                continue
            if not explicit and manifest.platforms and current_platform() not in manifest.platforms:
                continue
            yield tid, manifest

    def check_ids(self, tool_ids: list[str] | None = None) -> tuple[list[str], list[str]]:
        """Return ``(satisfied, missing)`` ids without installing anything.

        Same id selection and abstract handling as :meth:`prepare_ids`.
        """
        satisfied: list[str] = []
        missing: list[str] = []
        for tid, manifest in self._selected_manifests(tool_ids):
            (satisfied if self.is_satisfied(manifest) else missing).append(tid)
        return satisfied, missing

    def prepare(self, manifest: Manifest) -> None:
        """Ensure runtimes and tool installs for one manifest."""
        if manifest.abstract:
            raise self._abstract_error(manifest.id)
        self.resolve(manifest)

    def prepare_ids(self, tool_ids: list[str] | None = None) -> list[str]:
        """Prepare installs for given ids or all non-abstract manifests.

        When *tool_ids* is omitted, abstract surfaces and tools unsupported on
        this host are skipped. When the caller names ids explicitly, abstract
        (or unknown) ids raise :class:`InstallError` and an unsupported host
        raises :class:`UnsupportedPlatformError` instead of a silent no-op.
        """
        prepared: list[str] = []
        for tid, manifest in self._selected_manifests(tool_ids):
            self.prepare(manifest)
            prepared.append(tid)
        return prepared
