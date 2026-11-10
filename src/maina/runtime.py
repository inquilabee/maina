"""Composition Runtime: registry overlays, parsers, installers, language provider.

``SESSION`` is process-wide and not thread-safe. Concurrent threads that call
``register`` / ``use`` / ``runtime()`` without external synchronization can race.
"""

from __future__ import annotations

import atexit
import os
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from maina.backends import GithubBackend, NpmBackend, PathBackend, PipBackend, ScriptBackend
from maina.core.errors import RegisterError
from maina.core.parsers import ParserResolver, builtin_generals
from maina.core.registry import Registry
from maina.installer import Installer
from maina.mise import MiseRuntime

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from importlib.metadata import EntryPoint

    from maina.core.parsers import ParserInput
    from maina.core.protocols import InstallerBackend, LanguageRuntimeProvider


class RuntimeSession:
    """Process-wide active composition Runtime holder."""

    def __init__(self) -> None:
        self.current: Runtime | None = None
        self._atexit_hook: Callable[[], None] | None = None

    def get(self) -> Runtime:
        """Return the active Runtime, lazy-building preferred defaults."""
        return self.current if self.current is not None else self.set(Runtime.preferred())

    def set(self, rt: Runtime) -> Runtime:
        """Replace the active Runtime and return it.

        Registers ``rt.close`` at exit, matching :meth:`get`'s lazy default —
        otherwise a ``Runtime`` installed explicitly via ``use(rt)`` never gets
        its ``GithubBackend`` / ``requests.Session`` closed. The previous
        hook is unregistered first: without this, swapping the active
        ``Runtime`` repeatedly (tests, a long-lived app reconfiguring) would
        accumulate one ``atexit`` closure per swap, keeping every superseded
        ``Runtime`` alive until process exit instead of just the current one.
        """
        if self._atexit_hook is not None:
            atexit.unregister(self._atexit_hook)
        self.current = rt
        self._atexit_hook = rt.close
        atexit.register(self._atexit_hook)
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
        replace: bool = False,
    ) -> Runtime:
        """Append/override plugins on this instance. Returns self.

        ``replace=True`` clears the existing registry roots before adding
        ``registry_dirs`` (a reset for tests / apps that reconfigure at runtime).
        """
        if registry_dirs is not None:
            # Validate before clearing: a bad path must not leave zero roots.
            dirs = self.require_dirs(self.as_paths(registry_dirs), kind="registry")
            if replace:
                self._registry.roots.clear()
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
    def preferred(
        cls,
        *,
        cache_root: Path | None = None,
        language: LanguageRuntimeProvider | None = None,
        registry_dirs: Path | str | Sequence[Path | str] | None = None,
        use_env: bool = True,
        discover: bool = False,
    ) -> Runtime:
        """Build a Runtime with builtin parsers, install backends, and mise.

        maina ships no tool catalog. With ``use_env`` (default) the cache root
        falls back to ``MAINA_CACHE`` and ``MAINA_REGISTRY`` (``os.pathsep``
        directories) is registered; pass ``use_env=False`` for a hermetic
        Runtime that ignores both. ``discover=True`` also registers every
        catalog published to the ``maina.registries`` entry-point group.
        """
        rt = cls(
            cache_root=cls._preferred_cache_root(cache_root, use_env=use_env), language=language
        )
        register(
            rt,
            parsers=builtin_generals(),
            installers=[
                PathBackend(),
                GithubBackend(),
                NpmBackend(),
                PipBackend(),
                ScriptBackend(),
            ],
            language=language or MiseRuntime(cache_root=rt.cache_root),
        )
        if dirs := cls._preferred_registry_dirs(registry_dirs, use_env=use_env, discover=discover):
            register(rt, registry_dirs=dirs)
        return rt

    @classmethod
    def _discovered_registry_dirs(cls) -> list[Path]:
        """Directories published to the ``maina.registries`` entry-point group."""
        from importlib.metadata import entry_points

        eps = entry_points(group="maina.registries")
        out: list[Path] = []
        for ep in eps:
            path = cls._load_registry_entry_point(ep)
            if path is None:
                continue
            if path.is_dir():
                out.append(path)
            else:
                warnings.warn(
                    f"maina.registries entry {ep.name!r} is not a directory, ignoring: {path}",
                    RuntimeWarning,
                    stacklevel=3,
                )
        return out

    @staticmethod
    def _load_registry_entry_point(ep: EntryPoint) -> Path | None:
        """Resolve one entry point to a Path; warn and return None if it is broken.

        A published catalog is third-party code, so ``ep.load()`` can raise
        anything on import — isolate it rather than let it escape ``discover``.
        """
        try:
            value = ep.load()
            return Path(str(value() if callable(value) else value))
        except Exception as exc:  # ruff:ignore[blind-except] - untrusted plugin import
            warnings.warn(
                f"maina.registries entry {ep.name!r} failed to load, ignoring: {exc}",
                RuntimeWarning,
                stacklevel=3,
            )
            return None

    @classmethod
    def _preferred_cache_root(cls, cache_root: Path | None, *, use_env: bool) -> Path:
        if cache_root is not None:
            return Path(cache_root)
        env_default = cls.resolve_cache_root(None) if use_env else None
        return env_default or Path.home() / ".cache" / "maina"

    @staticmethod
    def _env_registry_dirs() -> list[Path]:
        """``MAINA_REGISTRY`` dirs; a set-but-missing entry warns instead of vanishing."""
        out: list[Path] = []
        for part in os.environ.get("MAINA_REGISTRY", "").split(os.pathsep):
            if not part:
                continue
            path = Path(part)
            if path.is_dir():
                out.append(path)
            else:
                warnings.warn(
                    f"MAINA_REGISTRY entry is not a directory, ignoring: {part!r}",
                    RuntimeWarning,
                    stacklevel=3,
                )
        return out

    @classmethod
    def _preferred_registry_dirs(
        cls,
        registry_dirs: Path | str | Sequence[Path | str] | None,
        *,
        use_env: bool,
        discover: bool = False,
    ) -> list[Path]:
        dirs: list[Path] = []
        if use_env:
            dirs += cls._env_registry_dirs()
        if discover:
            dirs += cls._discovered_registry_dirs()
        if registry_dirs is not None:
            dirs += cls.as_paths(registry_dirs)
        return dirs

    def close(self) -> None:
        """Release pooled resources (e.g. HTTP sessions) held by backends."""
        for backend in self._installer.backends:
            closer = getattr(backend, "close", None)
            if callable(closer):
                closer()

    def __enter__(self) -> Runtime:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @staticmethod
    def resolve_cache_root(path: Path | None = None) -> Path:
        """Resolve the maina cache root (``MAINA_CACHE`` / ``~/.cache/maina``).

        Always absolute (``expanduser().resolve()``): a relative cache root
        would make ``argv[0]`` relative and break a run with an explicit ``cwd``.
        """
        if path is not None:
            root = Path(path)
        else:
            override = os.environ.get("MAINA_CACHE")
            root = Path(override) if override else Path.home() / ".cache" / "maina"
        root = root.expanduser().resolve()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RegisterError(
                f"cannot create the maina cache directory {root}: {exc} "
                "(set MAINA_CACHE to a writable directory)"
            ) from exc
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
    replace: bool = False,
    runtime: Runtime | None = None,
) -> Runtime:
    """Mutate a Runtime (positional or ``runtime=``) or the active session; return it.

    Low-level composition helper — returns the bare ``Runtime``. Most callers want
    the public ``maina.register(...)`` (:func:`maina.tool.register`), which wraps this
    and returns a :class:`~maina.tool.MainaApp` bound to a ``Tool()`` factory instead.
    Passing both ``rt`` and ``runtime=`` raises ``ValueError``. With neither, mutates
    ``SESSION.get()``.
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
        replace=replace,
    )
