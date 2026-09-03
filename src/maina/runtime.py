"""Composition Runtime: registry overlays, parsers, installers, language provider.

``SESSION`` is process-wide and not thread-safe. Concurrent threads that call
``register`` / ``use`` / ``runtime()`` without external synchronization can race.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from maina.backends import GithubBackend, NpmBackend, PathBackend
from maina.core.errors import RegisterError
from maina.core.parsers import ParserResolver, builtin_generals
from maina.core.registry import Registry
from maina.installer import Installer
from maina.mise import MiseRuntime

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

    from maina.core.parsers import ParserInput
    from maina.core.protocols import InstallerBackend, LanguageRuntimeProvider


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


class Runtime:
    """Owns registry overlays, general parsers, installer backends, and language.

    This is the composition root. Language runtimes stay behind
    :class:`~maina.core.protocols.LanguageRuntimeProvider` (e.g. ``MiseRuntime``).
    """

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        language: LanguageRuntimeProvider | None = None,
    ) -> None:
        self._cache_root = self.resolve_cache_root(cache_root)
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
        parsers: Mapping[str, ParserInput] | None = None,
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
        """Build the preferred Runtime: builtin parsers, backends, and mise.

        maina ships no tool catalog. ``preferred()`` has an empty registry
        unless ``MAINA_REGISTRY`` names one or more existing directories
        (``os.pathsep``-separated); the caller can also add one later with
        ``register(rt, registry_dirs=[...])``.
        """
        rt = cls()
        register(
            rt,
            parsers=builtin_generals(),
            installers=[
                PathBackend(),
                GithubBackend(),
                NpmBackend(),
            ],
            language=MiseRuntime(cache_root=rt.cache_root),
        )
        if env_dirs := [
            Path(part)
            for part in os.environ.get("MAINA_REGISTRY", "").split(os.pathsep)
            if part and Path(part).is_dir()
        ]:
            register(rt, registry_dirs=env_dirs)
        return rt

    @staticmethod
    def resolve_cache_root(path: Path | None = None) -> Path:
        """Resolve the maina cache root (``MAINA_CACHE`` / ``~/.cache/maina``)."""
        if path is not None:
            root = Path(path)
        else:
            override = os.environ.get("MAINA_CACHE")
            root = Path(override) if override else Path.home() / ".cache" / "maina"
        root.mkdir(parents=True, exist_ok=True)
        return root

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


# Back-compat alias for tests/callers that import the module-level helper.
resolve_cache_root = Runtime.resolve_cache_root


def runtime() -> Runtime:
    """Return the process-wide active composition Runtime (lazy preferred default)."""
    return SESSION.get()


def use(rt: Runtime) -> Runtime:
    """Replace the process-wide active composition Runtime."""
    return SESSION.set(rt)


@contextmanager
def isolated(rt: Runtime | None = None) -> Iterator[Runtime]:
    """Swap the active Runtime for the block, restoring the prior one on exit.

    ``with maina.isolated(rt): ...`` scopes an alternate catalog to a block
    without leaking it into the rest of the process. For concurrent isolation
    prefer an explicit ``Tool(..., runtime=rt)`` — this swap is process-wide and
    not thread-safe.
    """
    previous = SESSION.current
    SESSION.set(rt if rt is not None else Runtime.preferred())
    try:
        yield SESSION.get()
    finally:
        SESSION.current = previous


def register(
    rt: Runtime | None = None,
    /,
    *,
    registry_dirs: Path | str | Sequence[Path | str] | None = None,
    parsers: Mapping[str, ParserInput] | None = None,
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
