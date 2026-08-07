"""PATH-backed installer backend."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from pyporter.core.errors import InstallError

if TYPE_CHECKING:
    from pyporter.core.protocols import InstallContext
    from pyporter.core.registry import Manifest


class PathBackend:
    """Resolve a CLI from PATH (``install.backend: path``)."""

    def __init__(self) -> None:
        self._which = shutil.which

    @property
    def name(self) -> str:
        return "path"

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        del version, ctx
        found = self._which(manifest.cli, path=env.get("PATH"))
        if not found:
            raise InstallError(f"{manifest.cli!r} not found on PATH")
        return found, env
