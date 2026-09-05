"""pip package installer backend."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from maina.core.errors import InstallError
from maina.core.protocols import InstallerBackend
from maina.core.subprocess_utils import resolve_within, run_or_wrap

if TYPE_CHECKING:
    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest

# Interpreter names to look for on the composed PATH, most specific first.
PYTHON_NAMES = ("python3", "python")


class PipBackend(InstallerBackend):
    """Install a Python package into a per-tool venv (``install.backend: pip``).

    One venv per tool per version, under the tool's own cache directory — not a
    shared environment. Two tools whose dependencies disagree cannot break each
    other, and a tool pinned to a different interpreter is just another cache
    directory rather than a special case.
    """

    @property
    def name(self) -> str:
        return "pip"

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        exe = self._resolve_pip(manifest, version, env, ctx)
        return str(exe), ctx.prepend_path(env, exe.parent)

    @staticmethod
    def probe(manifest: Manifest, *, version: str, ctx: InstallContext) -> bool:
        """True when the venv's entry point already exists (no install)."""
        return PipBackend.entry_point(ctx.cache_root, manifest.cli, version).exists()

    @staticmethod
    def entry_point(cache_root: Path, cli: str, version: str) -> Path:
        """Path the tool's console script lands at inside its venv."""
        venv = cache_root / "tools" / cli / version / "pip"
        return venv / "Scripts" / f"{cli}.exe" if sys.platform == "win32" else venv / "bin" / cli

    def _resolve_pip(
        self,
        manifest: Manifest,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> Path:
        """Install the package into its own venv and return the entry point.

        *version* is the identity-keyed cache-dir segment; the version actually
        installed comes from ``manifest.version``.
        """
        entry = self.entry_point(ctx.cache_root, manifest.cli, version)
        if entry.exists():
            return entry
        pinned = manifest.version
        if not pinned:
            raise InstallError(
                f"{manifest.id}: install.backend: pip needs a version: to install "
                "(an unpinned lint tool changes its findings under you)"
            )
        venv = entry.parent.parent
        python = self._venv_python(manifest, venv, env, ctx)
        package = manifest.install.package or manifest.cli
        self._pip_install(
            manifest, python, f"{package}=={pinned}", env, allow_sdist=manifest.install.allow_sdist
        )
        return self._verify_entry_point(manifest, entry, venv)

    @staticmethod
    def _verify_entry_point(manifest: Manifest, entry: Path, venv: Path) -> Path:
        """The install must have produced an executable inside this venv."""
        if not (entry.is_file() or entry.is_symlink()):
            raise InstallError(
                f"{manifest.id}: installing {manifest.install.package or manifest.cli} produced no "
                f"{manifest.cli!r} entry point (is the console script named something else? "
                "set cli: to the script name and install.package: to the distribution)"
            )
        resolve_within(
            entry,
            venv,
            error_cls=InstallError,
            message=(
                f"{manifest.id}: {manifest.cli} resolved to {entry.resolve()}, outside the tool "
                f"cache ({venv}); refusing to run an out-of-cache install"
            ),
        )
        return entry

    def _venv_python(
        self,
        manifest: Manifest,
        venv: Path,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> Path:
        """Create the tool's venv and return the interpreter inside it."""
        base = self._base_interpreter(manifest, env, ctx)
        venv.parent.mkdir(parents=True, exist_ok=True)
        run_or_wrap(
            [str(base), "-m", "venv", str(venv)],
            env=env,
            timeout=self.VENV_TIMEOUT,
            error_cls=InstallError,
            on_timeout=lambda: f"{manifest.id}: creating a venv timed out",
            on_failure=lambda detail: f"{manifest.id}: could not create a venv at {venv}: {detail}",
        )
        inside = venv / "Scripts" / "python.exe" if sys.platform == "win32" else venv / "bin/python"
        if not inside.is_file():
            raise InstallError(f"{manifest.id}: venv at {venv} has no interpreter")
        return inside

    @staticmethod
    def _base_interpreter(manifest: Manifest, env: dict[str, str], ctx: InstallContext) -> Path:
        """The interpreter to build the tool's venv from.

        A ``requires: {python: "3.13"}`` pin is provisioned through the language
        provider like any other runtime, so one tool can sit on a different
        interpreter from the rest without a shared environment to negotiate.
        With no pin, the interpreter running maina is used — pinning is opt-in,
        so a catalog that does not care pays no provisioning cost.
        """
        pinned = manifest.requires.get("python")
        if pinned is None:
            return Path(sys.executable)
        # A pin is never satisfied by whatever ``python3`` happens to be on PATH:
        # that silently builds the venv on the wrong interpreter, which is the one
        # thing the pin exists to prevent. Resolve it through the language
        # provider and take the interpreter out of *that* bin dir.
        # Hand mise the trimmed install env so its subprocesses honour env_allow.
        bin_dir = ctx.language.ensure("python", pinned, base_env=env)
        ctx.prepend_path(env, bin_dir)
        for name in PYTHON_NAMES:
            candidate = bin_dir / name
            if candidate.is_file():
                return candidate
        raise InstallError(
            f"{manifest.id}: python {pinned} provisioned to {bin_dir}, which holds no "
            f"{' / '.join(PYTHON_NAMES)}"
        )

    VENV_TIMEOUT = 120
    INSTALL_TIMEOUT = 600

    @staticmethod
    def _pip_install(
        manifest: Manifest,
        python: Path,
        spec: str,
        env: dict[str, str],
        *,
        allow_sdist: bool = False,
    ) -> None:
        """Install one pinned package into the tool's venv.

        ``--only-binary=:all:`` by default: building an sdist executes the
        package's own ``setup.py`` with the composed environment, which is the
        same exposure npm lifecycle scripts carry — so a manifest must opt in
        with ``install.allow_sdist: true``. The index is never manifest-settable;
        a manifest must not be able to redirect where a package comes from.
        """
        argv = [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-input",
            "--disable-pip-version-check",
        ]
        if not allow_sdist:
            argv.append("--only-binary=:all:")
        argv.extend(["--", spec])
        run_or_wrap(
            argv,
            env=env,
            timeout=PipBackend.INSTALL_TIMEOUT,
            error_cls=InstallError,
            on_timeout=(
                lambda: (
                    f"{manifest.id}: pip install {spec} timed out "
                    f"after {PipBackend.INSTALL_TIMEOUT}s"
                )
            ),
            on_failure=lambda detail: f"{manifest.id}: pip install {spec} failed: {detail}",
        )
