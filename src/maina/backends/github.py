"""GitHub release asset installer backend."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

from maina.backends.archive import ArchiveFetcher
from maina.core.errors import InstallError
from maina.core.platform import platform_key
from maina.core.protocols import InstallerBackend
from maina.core.registry.common import is_unsafe_segment

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest


GITHUB_DOWNLOAD_HOSTS = frozenset(
    {"github.com", "api.github.com", "githubusercontent.com", "codeload.github.com"}
)


class GithubBackend(InstallerBackend):
    """Download a GitHub release asset into the tool cache (``backend: github``)."""

    def __init__(self, fetcher: ArchiveFetcher | None = None) -> None:
        self._fetcher = fetcher or ArchiveFetcher(allowed_redirect_hosts=GITHUB_DOWNLOAD_HOSTS)

    @property
    def name(self) -> str:
        return "github"

    def close(self) -> None:
        """Close the shared HTTP session."""
        self._fetcher.close()

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        exe = self._resolve_github(manifest, version, ctx)
        return str(exe), ctx.prepend_path(env, exe.parent)

    @staticmethod
    def probe(manifest: Manifest, *, version: str, ctx: InstallContext) -> bool:
        """True when the cached release binary already exists (no download)."""
        binary = manifest.install.binary or manifest.cli
        cached = ctx.cache_root / "tools" / manifest.cli / version / "bin" / binary
        return cached.is_file()

    def _resolve_github(self, manifest: Manifest, version: str, ctx: InstallContext) -> Path:
        """Download a GitHub release asset into the tool cache if needed."""
        repo, binary = self._install_parts(manifest)
        key = platform_key()
        asset = manifest.install.asset_for(key, manifest.version)
        if not asset:
            raise InstallError(f"{manifest.id}: no github asset for {key}")
        if is_unsafe_segment(asset):
            raise InstallError(
                f"{manifest.id}: asset name {asset!r} for {key} is not a safe path segment "
                "(check install.asset_template and os_map / arch_map)"
            )

        dest_dir = ctx.ensure_tool_bin_dir(manifest.cli, version)
        dest = dest_dir / binary
        if dest.is_file():
            return dest

        # ``version`` is the identity-keyed cache segment; the release tag is the
        # manifest's own version (or an explicit ``install.tag``).
        tag = manifest.install.tag or manifest.version
        url = self._release_url(repo, str(asset), tag=str(tag) if tag else None)
        self._fetcher.download_and_extract(
            url,
            dest_dir,
            binary=binary,
            asset_name=str(asset),
            sha256=manifest.install.sha256.get(key),
            allow_any_executable=manifest.install.allow_any_executable,
        )
        if not dest.is_file():
            raise InstallError(
                f"{manifest.id}: downloaded asset but {binary} missing in {dest_dir}"
            )
        return dest

    @staticmethod
    def _install_parts(manifest: Manifest) -> tuple[str, str]:
        """Validate and return repo and binary name."""
        install = manifest.install
        repo = install.repo
        binary = install.binary or manifest.cli
        if not repo or not install.has_assets():
            raise InstallError(
                f"{manifest.id}: github install needs repo and assets (or asset_template)"
            )
        return repo, binary

    @staticmethod
    def _release_url(repo: str, asset: str, *, tag: str | None) -> str:
        """Build a GitHub release download URL."""
        asset_quoted = quote(asset, safe="")
        tag_quoted = quote(tag or "", safe="")
        latest = f"https://github.com/{repo}/releases/latest/download/{asset_quoted}"
        pinned = f"https://github.com/{repo}/releases/download/{tag_quoted}/{asset_quoted}"
        return pinned if tag and tag != "latest" else latest
