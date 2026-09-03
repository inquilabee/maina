"""GitHub release asset installer backend."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from maina.backends.archive import ArchiveFetcher
from maina.core.errors import InstallError
from maina.core.platform import platform_key

if TYPE_CHECKING:
    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest


GITHUB_DOWNLOAD_HOSTS = frozenset(
    {"github.com", "api.github.com", "githubusercontent.com", "codeload.github.com"}
)


class GithubBackend:
    """Download a GitHub release asset into the tool cache (``backend: github``)."""

    def __init__(self, fetcher: ArchiveFetcher | None = None) -> None:
        self._fetcher = fetcher or ArchiveFetcher(allowed_redirect_hosts=GITHUB_DOWNLOAD_HOSTS)

    @property
    def name(self) -> str:
        return "github"

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
        repo, assets, binary = self._install_parts(manifest)
        key = platform_key()
        asset = assets.get(key)
        if not asset:
            raise InstallError(f"{manifest.id}: no github asset for {key}")

        dest_dir = ctx.tool_bin_dir(manifest.cli, version)
        dest = dest_dir / binary
        if dest.is_file():
            return dest

        tag = manifest.install.tag or version
        url = self._release_url(repo, str(asset), tag=str(tag) if tag else None)
        self._fetcher.download_and_extract(
            url,
            dest_dir,
            binary=binary,
            asset_name=str(asset),
            sha256=manifest.install.sha256.get(key),
        )
        self._finalize_binary(manifest.id, dest, dest_dir, binary, str(asset))
        return dest

    @staticmethod
    def _install_parts(manifest: Manifest) -> tuple[str, dict[str, Any], str]:
        """Validate and return repo, assets map, and binary name."""
        install = manifest.install
        repo = install.repo
        assets = install.assets
        binary = install.binary or manifest.cli
        if not repo or not assets:
            raise InstallError(f"{manifest.id}: github install needs repo and assets")
        return repo, assets, binary

    @staticmethod
    def _finalize_binary(
        tool_id: str,
        dest: Path,
        dest_dir: Path,
        binary: str,
        asset: str,
    ) -> None:
        """Rename bare assets and ensure the binary is executable."""
        if not dest.is_file():
            candidate = dest_dir / Path(asset).name
            if candidate.is_file() and candidate.name != binary:
                candidate.rename(dest)
            if not dest.is_file():
                raise InstallError(
                    f"{tool_id}: downloaded asset but {binary} missing in {dest_dir}"
                )
        ArchiveFetcher.make_executable(dest)

    @staticmethod
    def _release_url(repo: str, asset: str, *, tag: str | None) -> str:
        """Build a GitHub release download URL."""
        latest = f"https://github.com/{repo}/releases/latest/download/{asset}"
        pinned = f"https://github.com/{repo}/releases/download/{tag}/{asset}"
        return pinned if tag and tag != "latest" else latest
