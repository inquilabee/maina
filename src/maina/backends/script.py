"""Local script/directory installer backend."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maina.core.errors import InstallError
from maina.core.protocols import InstallerBackend
from maina.core.subprocess_utils import resolve_within

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest


class ScriptBackend(InstallerBackend):
    """Run a repo-local script or script package (``install.backend: script``).

    ``install.entry`` names a file or a directory under a co-located
    ``scripts/`` family dir (or, with a ``/``, under the registry root — see
    ``Registry._resolve_entry_path``); the loader already resolved it to
    ``manifest.entry_path`` and checked it exists inside its registry root.

    A single file is chmod +x'd and exec'd directly — its own shebang
    (``#!/usr/bin/env python3``, ``#!/usr/bin/env bash``, ...) picks the
    interpreter, and a ``requires:``-pinned runtime is already on ``PATH`` by
    the time this runs (the installer composes ``requires:`` env before
    calling any backend), so the shebang resolves to the pinned interpreter
    without maina having to invoke it explicitly. A directory must contain
    exactly one file named like the directory itself (any or no extension —
    the ``<name>/<name>.<ext>`` convention manifests already use for a family
    root), which is the entry point; sibling files in the directory are free
    to import/source each other however their language allows.
    """

    @property
    def name(self) -> str:
        return "script"

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        del version, ctx
        if manifest.entry_path is None:
            raise InstallError(f"{manifest.id!r}: install.entry is required for the script backend")
        target = self._entry_file(manifest.entry_path, manifest.id)
        self._require_shebang(target, manifest.id)
        self._ensure_executable(target)
        return str(target), env

    @staticmethod
    def probe(manifest: Manifest, *, version: str, ctx: InstallContext) -> bool:
        """True when ``entry_path`` resolved at load time — no side effects."""
        del version, ctx
        return manifest.entry_path is not None

    @staticmethod
    def _entry_file(path: Path, tool_id: str) -> Path:
        """*path* itself if it is a file; otherwise the one ``<dirname>.*``
        file inside it that is the directory's entry point.

        The loader already checked ``path`` itself stays inside the registry
        root, but a directory's own listing is a fresh filesystem lookup — a
        member matched here could be a symlink pointing outside ``path``, so
        it gets its own containment check before anything execs it.
        """
        if path.is_file():
            return path
        matches = [p for p in path.iterdir() if p.is_file() and p.stem == path.name]
        if len(matches) != 1:
            raise InstallError(
                f"{tool_id!r}: script entry dir {path} must contain exactly one file "
                f"named {path.name!r} (any/no extension) as its entry point; found "
                f"{len(matches)}"
            )
        entry = matches[0]
        resolve_within(
            entry,
            path,
            error_cls=InstallError,
            message=f"{tool_id!r}: script entry {entry} escapes {path}",
        )
        return entry

    @staticmethod
    def _require_shebang(path: Path, tool_id: str) -> None:
        """A missing shebang would otherwise fail as an opaque OS-level "Exec
        format error" the first time the tool actually runs."""
        if path.read_bytes()[:2] != b"#!":
            raise InstallError(
                f"{tool_id!r}: script entry {path} has no shebang line "
                "(e.g. #!/usr/bin/env python3) — required so the OS knows which "
                "interpreter to run it with"
            )

    @staticmethod
    def _ensure_executable(path: Path) -> None:
        mode = path.stat().st_mode
        if not mode & 0o111:
            try:
                path.chmod(mode | 0o111)
            except OSError as exc:
                raise InstallError(f"cannot make {path} executable: {exc}") from exc
