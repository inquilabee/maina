"""npm package installer backend."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from pyporter.errors import InstallError

if TYPE_CHECKING:
    from pathlib import Path

    from pyporter.protocols import InstallContext
    from pyporter.registry import Manifest


class NpmBackend:
    """Install an npm package into the tool cache (``install.backend: npm``)."""

    @property
    def name(self) -> str:
        return "npm"

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        exe = self._resolve_npm(manifest, version, env, ctx)
        return str(exe), ctx.prepend_path(env, exe.parent)

    def _resolve_npm(
        self,
        manifest: Manifest,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> Path:
        """Install an npm package into the tool cache and return its bin path."""
        package = manifest.install.package or manifest.cli
        self._ensure_node_on_path(manifest, env, ctx)
        prefix = ctx.cache_root / "tools" / manifest.cli / version / "npm"
        link = prefix / "node_modules" / ".bin" / manifest.cli
        return (
            link
            if link.exists()
            else self._install_package(manifest.cli, package, version, prefix, link, env)
        )

    @staticmethod
    def _ensure_node_on_path(
        manifest: Manifest,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> None:
        """Ensure a language-provider Node runtime is on PATH."""
        node_version = manifest.requires.version or "20"
        if shutil.which("node", path=env.get("PATH")):
            return
        ctx.language.ensure("node", node_version)
        env |= ctx.language.env("node", node_version)

    @staticmethod
    def _install_package(
        cli: str,
        package: str,
        version: str,
        prefix: Path,
        link: Path,
        env: dict[str, str],
    ) -> Path:
        """npm install a package into prefix and return its bin link."""
        prefix.mkdir(parents=True, exist_ok=True)
        pkg_spec = f"{package}@{version}" if version and version != "default" else package
        npm = shutil.which("npm", path=env.get("PATH"))
        if not npm:
            raise InstallError("npm not found; ensure node runtime via mise")
        NpmBackend._npm_install(npm, prefix, pkg_spec, env)
        if link.exists():
            return link
        raise InstallError(f"npm install of {pkg_spec} did not produce {cli}")

    @staticmethod
    def _npm_install(npm: str, prefix: Path, pkg_spec: str, env: dict[str, str]) -> None:
        """Run npm install --prefix for a package."""
        try:
            subprocess.run(
                [npm, "install", "--prefix", str(prefix), pkg_spec],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise InstallError(f"npm install {pkg_spec} failed: {exc.stderr or exc}") from exc
