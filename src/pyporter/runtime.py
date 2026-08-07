"""Composition Runtime: registry overlays, parsers, installers, language provider.

``SESSION`` is process-wide and not thread-safe. Concurrent threads that call
``register`` / ``use`` / ``runtime()`` without external synchronization can race.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from pyporter.backends import GithubBackend, NpmBackend, PathBackend
from pyporter.errors import RegisterError
from pyporter.installer import Installer
from pyporter.mise import MiseRuntime
from pyporter.parsers import ParserFn, ParserResolver, builtin_generals
from pyporter.registry import Registry

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyporter.protocols import InstallerBackend, LanguageRuntimeProvider


class RuntimeSession:
    """Process-wide active composition Runtime holder."""

    def __init__(self) -> None:
        self.current: Runtime | None = None

    def get(self) -> Runtime:
        """Return the active Runtime, lazy-building preferred defaults."""
        if self.current is None:
            self.current = Runtime.preferred()
        return self.current

    def set(self, rt: Runtime) -> Runtime:
        """Replace the active Runtime and return it."""
        self.current = rt
        return rt


SESSION = RuntimeSession()


def resolve_cache_root(path: Path | None = None) -> Path:
    """Resolve the pyporter cache root (``PYPORTER_CACHE`` / ``~/.cache/pyporter``)."""
    if path is not None:
        root = Path(path)
    else:
        override = os.environ.get("PYPORTER_CACHE")
        root = Path(override) if override else Path.home() / ".cache" / "pyporter"
    root.mkdir(parents=True, exist_ok=True)
    return root


class Runtime:
    """Owns registry overlays, general parsers, installer backends, and language.

    This is the composition root. Language runtimes stay behind
    :class:`~pyporter.protocols.LanguageRuntimeProvider` (e.g. ``MiseRuntime``).
    """

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        language: LanguageRuntimeProvider | None = None,
    ) -> None:
        self._cache_root = resolve_cache_root(cache_root)
        self._registry = Registry([])
        self._parsers = ParserResolver()
        self._language: LanguageRuntimeProvider | None = language
        self._installer = Installer(
            cache_root=self._cache_root,
            language=language,
            registry=self._registry,
            backends=[],
        )
        if language is not None:
            self._installer.language = language

    @property
    def registry(self) -> Registry:
        return self._registry

    @property
    def parsers(self) -> ParserResolver:
        return self._parsers

    @property
    def installer(self) -> Installer:
        return self._installer

    @property
    def language(self) -> LanguageRuntimeProvider:
        if self._language is None:
            self._language = MiseRuntime(cache_root=self._cache_root)
            self._installer.language = self._language
        return self._language

    @property
    def cache_root(self) -> Path:
        return self._cache_root

    def register(
        self,
        *,
        registry_dirs: Path | str | Sequence[Path | str] | None = None,
        parsers: Mapping[str, ParserFn] | None = None,
        parsers_dirs: Path | str | Sequence[Path | str] | None = None,
        installers: Sequence[InstallerBackend] | None = None,
        language: LanguageRuntimeProvider | None = None,
    ) -> Runtime:
        """Append/override plugins on this instance. Returns self."""
        if registry_dirs is not None:
            dirs = self.require_dirs(self.as_paths(registry_dirs), kind="registry")
            self._registry.append_roots(dirs)
        if parsers is not None:
            self._parsers.register_many(parsers)
        if parsers_dirs is not None:
            dirs = self.require_dirs(self.as_paths(parsers_dirs), kind="parsers")
            self._parsers.load_parsers_dirs(dirs)
        if installers is not None:
            self._installer.register_backends(installers)
        if language is not None:
            self._language = language
            self._installer.language = language
        return self

    @classmethod
    def preferred(cls) -> Runtime:
        """Empty Runtime + bootstrap register of shipped defaults."""
        return cls._bootstrap_preferred()

    @classmethod
    def _bootstrap_preferred(cls) -> Runtime:
        """Build preferred Runtime by registering shipped plugins (same path as clients)."""
        rt = cls()
        shipped = Path(__file__).resolve().parent / "registry"
        return register(
            rt,
            registry_dirs=[shipped],
            parsers=builtin_generals(),
            installers=[
                PathBackend(),
                GithubBackend(),
                NpmBackend(),
            ],
            language=MiseRuntime(cache_root=rt.cache_root),
        )

    @staticmethod
    def as_paths(value: Path | str | Sequence[Path | str]) -> list[Path]:
        """Coerce a single path or sequence into a list of Paths."""
        return [Path(value)] if isinstance(value, (str, Path)) else [Path(p) for p in value]

    @staticmethod
    def require_dirs(paths: Sequence[Path], *, kind: str) -> list[Path]:
        """Fail fast when a register path is missing or not a directory."""
        resolved: list[Path] = []
        for path in paths:
            if not path.exists():
                raise RegisterError(f"{kind} path does not exist: {path}")
            if not path.is_dir():
                raise RegisterError(f"{kind} path is not a directory: {path}")
            resolved.append(path)
        return resolved


def runtime() -> Runtime:
    """Return the process-wide active composition Runtime (lazy preferred default)."""
    return SESSION.get()


def use(rt: Runtime) -> Runtime:
    """Replace the process-wide active composition Runtime."""
    return SESSION.set(rt)


def register(
    rt: Runtime | None = None,
    /,
    *,
    registry_dirs: Path | str | Sequence[Path | str] | None = None,
    parsers: Mapping[str, ParserFn] | None = None,
    parsers_dirs: Path | str | Sequence[Path | str] | None = None,
    installers: Sequence[InstallerBackend] | None = None,
    language: LanguageRuntimeProvider | None = None,
    runtime: Runtime | None = None,
) -> Runtime:
    """Mutate a Runtime (positional or ``runtime=``) or the active session; return it.

    Prefer ``register(rt, ...)``. ``runtime=`` remains for compatibility. Passing both
    raises ``ValueError``. With neither, mutates ``SESSION.get()``.
    """
    if rt is not None and runtime is not None:
        raise ValueError("pass Runtime positionally or as runtime=, not both")
    target = rt if rt is not None else runtime if runtime is not None else SESSION.get()
    return target.register(
        registry_dirs=registry_dirs,
        parsers=parsers,
        parsers_dirs=parsers_dirs,
        installers=installers,
        language=language,
    )
