"""PATH-backed installer backend."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from maina.core.errors import InstallError
from maina.core.protocols import InstallerBackend

if TYPE_CHECKING:
    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest


class PathBackend(InstallerBackend):
    """Resolve a CLI from PATH (``install.backend: path``)."""

    @property
    def name(self) -> str:
        return "path"

    def resolve(  # ruff:ignore[no-self-use] - must be an instance method, overrides InstallerBackend.resolve
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        del version, ctx
        found = shutil.which(manifest.cli, path=env.get("PATH"))
        if not found:
            raise InstallError(
                f"{manifest.cli!r} not found on PATH — install it, or add an install: block "
                "(github, npm, pip, script) so maina can fetch it"
            )
        return found, env

    @staticmethod
    def probe(manifest: Manifest, *, version: str, ctx: InstallContext) -> bool:
        """True when the CLI already resolves on PATH (no side effects).

        Searches ``ctx.probe_path`` — the ambient PATH plus the bin dirs of any
        already-installed ``requires:`` runtimes — so a tool provided by a pinned
        runtime is not reported ``missing`` by a check that ``resolve`` passes.
        """
        del version
        return shutil.which(manifest.cli, path=ctx.probe_path) is not None
