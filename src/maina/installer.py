"""Install and resolve tool executables into the maina cache."""

from __future__ import annotations

import importlib
import os
import warnings
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

# POSIX only; the advisory lock degrades to a no-op elsewhere (Windows).
try:
    _fcntl: Any = importlib.import_module("fcntl")
except ImportError:  # pragma: no cover - Windows
    _fcntl = None

from maina.core.envfilter import filter_env
from maina.core.errors import InstallError, ManifestError, UnsupportedPlatformError
from maina.core.platform import current_platform
from maina.core.protocols import InstallContext, InstallerBackend, LanguageRuntimeProvider
from maina.mise import MiseRuntime

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from maina.core.registry import Manifest, Registry, RequiresSpec

# Backends that key their cache by tool identity (repo/tag/asset/sha256), not
# just cli+version.
IDENTITY_BACKENDS = frozenset({"github", "npm"})


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
    def backends(self) -> list[InstallerBackend]:
        """Registered backends (dispatch order irrelevant; keyed by name)."""
        return list(self._backends.values())

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
        """Exclusive lock for a cache install directory (parallel prepare safety).

        Uses ``_fcntl.flock`` on POSIX; where ``fcntl`` is unavailable (Windows)
        the body runs without the advisory lock.
        """
        dest.mkdir(parents=True, exist_ok=True)
        if _fcntl is None:
            yield
            return
        lock_path = dest / ".maina-install.lock"
        with lock_path.open("a+b") as lock_file:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)

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

    @staticmethod
    def _cache_version(manifest: Manifest) -> str:
        """Cache dir segment: ``<version>`` or ``<version>-<identity8>``.

        github / npm tools include a short hash of repo/tag/asset/sha256/package
        so two manifests that share cli+version but differ in *what* they fetch
        do not collide (and a digest pin is not bypassed by a stale cache hit).
        """
        version = manifest.version or "default"
        return (
            f"{version}-{manifest.install.identity_key()}"
            if manifest.install.backend in IDENTITY_BACKENDS
            else version
        )

    def resolve(
        self, manifest: Manifest, *, env_allow: Iterable[str] | None = None
    ) -> tuple[str, dict[str, str]]:
        """Resolve argv[0] path/name and process env for a manifest.

        ``env_allow`` trims the environment handed to install-phase subprocesses
        (npm / mise) the same way it trims the tool's own environment.
        """
        self._require_supported_platform(manifest)
        backend_name = manifest.install.backend
        version = self._cache_version(manifest)
        # Trim os.environ first, then compose: the language provider runs its
        # provisioning subprocesses (mise / npm) with this trimmed env too, so a
        # secret gated out by ``env_allow`` never reaches them.
        base_env = dict(filter_env(dict(os.environ), env_allow))
        env = self._compose_requires_env(manifest.requires, base_env)
        backend = self._backends.get(backend_name)
        if backend is None:
            raise InstallError(f"unknown install backend: {backend_name!r}")
        ctx = InstallContext(cache_root=self.cache_root, language=self.language)
        lock_dir = self.cache_root / "tools" / manifest.cli / version
        # Defence in depth: cli/version are validated as safe segments at load,
        # but never lock or write outside the cache root.
        if not lock_dir.resolve().is_relative_to(self.cache_root.resolve()):
            raise InstallError(f"{manifest.id}: install path escapes the cache root")
        with self.install_dir_lock(lock_dir):
            return backend.resolve(manifest, version=version, env=env, ctx=ctx)

    def _compose_requires_env(
        self, requires: RequiresSpec, base_env: dict[str, str]
    ) -> dict[str, str]:
        """Ensure every pinned runtime and compose PATH without clobbering.

        ``LanguageRuntimeProvider.env`` typically copies ``os.environ`` per call,
        so invoking it once per pin would drop earlier bins. Mirror
        :meth:`MiseRuntime.env`: ``ensure`` each runtime (handing it *base_env* so
        its subprocesses honour ``env_allow``), prepend each bin dir, set
        ``MISE_DATA_DIR`` once when any pin is present.
        """
        env = dict(base_env)
        pins = requires.runtimes
        if not pins:
            return env
        for runtime, version in pins.items():
            bin_dir = self.language.ensure(runtime, version, base_env=env)
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
        satisfied: bool = probe(manifest, version=self._cache_version(manifest), ctx=ctx)
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
        omitted, abstract surfaces, hosts the manifest does not target, and
        manifests that fail to load are skipped (the last with a warning).
        """
        explicit = tool_ids is not None
        ids = tool_ids if explicit else self.registry.iter_ids()
        for tid in ids:
            manifest = self._resolve_selection(tid, explicit=explicit)
            if manifest is not None:
                yield tid, manifest

    def _resolve_selection(self, tid: str, *, explicit: bool) -> Manifest | None:
        """Load and filter one id for listing / prepare; ``None`` means skip.

        A broken manifest re-raises when the caller named it, else warns.
        """
        try:
            manifest = self.registry.load(tid)
        except ManifestError:
            if explicit:
                raise
            warnings.warn(
                f"skipping {tid!r}: manifest failed to load", RuntimeWarning, stacklevel=2
            )
            return None
        if manifest.abstract:
            if explicit:
                raise self._abstract_error(tid)
            return None
        wrong_host = (
            not explicit and manifest.platforms and current_platform() not in manifest.platforms
        )
        return None if wrong_host else manifest

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
