"""npm package installer backend."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

from maina.core.errors import InstallError

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest


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

    @staticmethod
    def probe(manifest: Manifest, *, version: str, ctx: InstallContext) -> bool:
        """True when the npm bin link already exists in the cache (no install)."""
        link = (
            ctx.cache_root
            / "tools"
            / manifest.cli
            / version
            / "npm"
            / "node_modules"
            / ".bin"
            / manifest.cli
        )
        return link.exists()

    def _resolve_npm(
        self,
        manifest: Manifest,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> Path:
        """Install an npm package into the tool cache and return its bin path.

        *version* is the identity-keyed cache-dir segment; the npm dist-tag /
        version comes from ``manifest.version``.
        """
        package = manifest.install.package or manifest.cli
        self._ensure_node_on_path(manifest, env, ctx)
        prefix = ctx.cache_root / "tools" / manifest.cli / version / "npm"
        link = prefix / "node_modules" / ".bin" / manifest.cli
        return (
            link
            if link.exists()
            else self._install_package(
                manifest.cli,
                package,
                manifest.version,
                prefix,
                link,
                env,
                allow_scripts=manifest.install.allow_scripts,
            )
        )

    @staticmethod
    def _ensure_node_on_path(
        manifest: Manifest,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> None:
        """Ensure Node is on *env* PATH without clobbering composed bins.

        ``LanguageRuntimeProvider.env`` typically rebuilds PATH from
        ``os.environ``, which would drop bins prepended by
        ``Installer._compose_requires_env``. Prefer ``ensure`` +
        :meth:`InstallContext.prepend_path` on the existing env.
        """
        node_version = manifest.requires.get("node") or "latest"
        if shutil.which("node", path=env.get("PATH")):
            return
        # Hand mise the trimmed install env so its subprocesses honour env_allow.
        bin_dir = ctx.language.ensure("node", node_version, base_env=env)
        ctx.prepend_path(env, bin_dir)

    @staticmethod
    def _install_package(
        cli: str,
        package: str,
        version: str | None,
        prefix: Path,
        link: Path,
        env: dict[str, str],
        *,
        allow_scripts: bool = False,
    ) -> Path:
        """npm install a package into prefix and return its bin link."""
        prefix.mkdir(parents=True, exist_ok=True)
        # Anchor the local project so npm does not walk up to a parent
        # package.json (or a global root) and resolve the bin to an outside
        # install.
        anchor = prefix / "package.json"
        if not anchor.exists():
            anchor.write_text(
                json.dumps({"name": f"maina-tool-{cli}", "private": True}) + "\n",
                encoding="utf-8",
            )
        pkg_spec = f"{package}@{version}" if version and version != "default" else package
        npm = shutil.which("npm", path=env.get("PATH"))
        if not npm:
            raise InstallError("npm not found; ensure node runtime via mise")
        NpmBackend._npm_install(npm, prefix, pkg_spec, env, allow_scripts=allow_scripts)
        if not (link.is_file() or link.is_symlink()):
            raise InstallError(f"npm install of {pkg_spec} did not produce {cli}")
        resolved = link.resolve()
        if not resolved.is_relative_to(prefix.resolve()):
            raise InstallError(
                f"npm resolved {cli} to {resolved}, outside the tool cache "
                f"({prefix}); refusing to link an out-of-cache install"
            )
        return link

    INSTALL_TIMEOUT = 600

    @staticmethod
    def _npm_install(
        npm: str,
        prefix: Path,
        pkg_spec: str,
        env: dict[str, str],
        *,
        allow_scripts: bool = False,
    ) -> None:
        """Run npm install --prefix for a package (local, no lockfile churn).

        ``--ignore-scripts`` by default: a third-party package's install /
        postinstall lifecycle scripts run with the composed environment, so a
        manifest must opt in with ``install.allow_scripts: true``.
        """
        argv = [
            npm,
            "install",
            "--prefix",
            str(prefix),
            "--no-fund",
            "--no-audit",
            "--no-package-lock",
        ]
        if not allow_scripts:
            argv.append("--ignore-scripts")
        argv.append(pkg_spec)
        try:
            subprocess.run(
                argv,
                check=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=NpmBackend.INSTALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstallError(
                f"npm install {pkg_spec} timed out after {NpmBackend.INSTALL_TIMEOUT}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise InstallError(f"npm install {pkg_spec} failed: {exc.stderr or exc}") from exc
