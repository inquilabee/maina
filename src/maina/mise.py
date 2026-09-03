"""mise-backed language runtime install and resolve."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from maina.core.errors import RuntimeProvisionError

# `mise.run` is an unpinned installer script. Downloading and piping it into a
# shell is opt-in: set MAINA_ALLOW_MISE_BOOTSTRAP=1 (or put `mise` on PATH).
BOOTSTRAP_ENV = "MAINA_ALLOW_MISE_BOOTSTRAP"
# Pin the mise release the bootstrap installs (mise.run honours MISE_VERSION).
PINNED_MISE_VERSION = "v2025.1.0"
# subprocess timeouts (seconds) for runtime provisioning.
MISE_INSTALL_TIMEOUT = 600
MISE_QUERY_TIMEOUT = 60


class MiseRuntime:
    """Instance-scoped owner of mise binary bootstrap and language runtimes.

    Satisfies :class:`~maina.core.protocols.LanguageRuntimeProvider`. This is the
    language runtime provider, not the composition :class:`~maina.runtime.Runtime`.
    """

    def __init__(self, cache_root: Path) -> None:
        root = Path(cache_root)
        root.mkdir(parents=True, exist_ok=True)
        self.cache_root = root

    def mise_bin(self) -> Path:
        """Locate mise on PATH or under the maina cache."""
        if found := shutil.which("mise"):
            return Path(found)
        cached = self.cache_root / "mise" / "bin" / "mise"
        if cached.is_file():
            return cached
        if os.environ.get(BOOTSTRAP_ENV, "").strip() not in {"1", "true", "yes"}:
            raise RuntimeProvisionError(
                "mise is required to provision a language runtime but was not found "
                f"on PATH. Install mise, or set {BOOTSTRAP_ENV}=1 to let maina run "
                "the mise.run installer (curl | sh)."
            )
        return self._bootstrap_mise(cached)

    def ensure(self, runtime: str, version: str) -> Path:
        """Ensure a mise-managed language runtime is installed; return its bin dir."""
        mise = self.mise_bin()
        spec = f"{runtime}@{version}"
        data_dir = self.cache_root / "mise-data"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["MISE_DATA_DIR"] = str(data_dir)

        if resolved := self._mise_which(mise, spec, runtime, env):
            return resolved

        try:
            subprocess.run(
                [str(mise), "install", spec],
                check=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=MISE_INSTALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeProvisionError(
                f"mise install {spec} timed out after {MISE_INSTALL_TIMEOUT}s"
            ) from exc
        except subprocess.CalledProcessError as exc:
            detail = exc.stderr or exc.stdout or str(exc)
            raise RuntimeProvisionError(f"mise install {spec} failed: {detail}") from exc

        resolved = self._mise_which(mise, spec, runtime, env)
        if resolved is not None:
            return resolved
        raise RuntimeProvisionError(f"could not resolve mise runtime binary for {spec}")

    def env(self, runtime: str, version: str) -> dict[str, str]:
        """Environment with mise runtime bin prepended to PATH."""
        bin_dir = self.ensure(runtime, version)
        env = os.environ.copy()
        env |= {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "MISE_DATA_DIR": str(self.cache_root / "mise-data"),
        }
        return env

    @staticmethod
    def _bootstrap_mise(target: Path) -> Path:
        """Download and install mise into the maina cache."""
        target.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env |= {"MISE_INSTALL_PATH": str(target), "MISE_VERSION": PINNED_MISE_VERSION}
        curl = shutil.which("curl")
        shell = shutil.which("sh")
        if not curl or not shell:
            raise RuntimeProvisionError("curl and sh are required to bootstrap mise")
        try:
            script = subprocess.run(
                [curl, "-fsSL", "https://mise.run"],
                check=True,
                capture_output=True,
                text=True,
                timeout=MISE_QUERY_TIMEOUT,
            )
            subprocess.run(
                [shell],
                input=script.stdout,
                check=True,
                env=env,
                capture_output=True,
                text=True,
                timeout=MISE_INSTALL_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeProvisionError("mise bootstrap timed out") from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeProvisionError(f"failed to bootstrap mise: {exc}") from exc
        if target.is_file():
            target.chmod(target.stat().st_mode | 0o111)
            return target
        if which := shutil.which("mise"):
            return Path(which)
        raise RuntimeProvisionError("mise bootstrap completed but binary not found")

    @staticmethod
    def _mise_which(mise: Path, spec: str, runtime: str, env: dict[str, str]) -> Path | None:
        """Resolve a runtime binary path via mise which/exec (None if unresolved)."""
        try:
            return MiseRuntime._mise_which_uncaught(mise, spec, runtime, env)
        except subprocess.TimeoutExpired:
            return None

    @staticmethod
    def _mise_which_uncaught(
        mise: Path, spec: str, runtime: str, env: dict[str, str]
    ) -> Path | None:
        which = subprocess.run(
            [str(mise), "which", "--toolchain", spec, runtime],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=MISE_QUERY_TIMEOUT,
        )
        if which.returncode == 0 and which.stdout.strip():
            return Path(which.stdout.strip()).parent

        shell = shutil.which("sh") or "/bin/sh"
        exe = subprocess.run(
            [str(mise), "exec", spec, "--", shell, "-c", f"command -v {runtime}"],
            check=False,
            env=env,
            capture_output=True,
            text=True,
            timeout=MISE_QUERY_TIMEOUT,
        )
        if exe.returncode == 0 and (path := exe.stdout.strip()):
            return Path(path).parent
        return None
