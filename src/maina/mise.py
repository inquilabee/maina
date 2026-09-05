"""mise-backed language runtime install and resolve."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from maina.core.errors import RuntimeProvisionError
from maina.core.subprocess_utils import (
    INSTALL_OUTPUT_MAX_BYTES,
    OutputCapExceededError,
    run_capped,
    run_or_wrap_capped,
)

# `mise.run` is an unpinned installer script. Downloading and piping it into a
# shell is opt-in: set MAINA_ALLOW_MISE_BOOTSTRAP=1 (or put `mise` on PATH).
BOOTSTRAP_ENV = "MAINA_ALLOW_MISE_BOOTSTRAP"
# Pin the mise release the bootstrap installs (mise.run honours MISE_VERSION).
# Bump periodically — verified current via `gh api repos/jdx/mise/releases/latest`
# (or https://github.com/jdx/mise/releases/latest) at the time this was last set.
PINNED_MISE_VERSION = "v2026.9.1"
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

    @property
    def data_dir(self) -> Path:
        """``MISE_DATA_DIR`` — mise's per-cache install tree."""
        return self.cache_root / "mise-data"

    @staticmethod
    def _seed_env(base_env: dict[str, str] | None) -> dict[str, str]:
        """Start from the caller's trimmed env, else the full ``os.environ``."""
        return dict(base_env) if base_env is not None else os.environ.copy()

    def _cached_mise_path(self) -> Path:
        return self.cache_root / "mise" / "bin" / "mise"

    def _mise_present(self) -> Path | None:
        """mise's binary path if already resolvable on PATH or in the cache.

        No install, no bootstrap — the one presence check both :meth:`mise_bin`
        and :meth:`present_bin_dir` need, kept in one place so it cannot drift
        into two independently-maintained copies.
        """
        if found := shutil.which("mise"):
            return Path(found)
        cached = self._cached_mise_path()
        return cached if cached.is_file() else None

    def mise_bin(self, *, base_env: dict[str, str] | None = None) -> Path:
        """Locate mise on PATH or under the maina cache."""
        if (found := self._mise_present()) is not None:
            return found
        if os.environ.get(BOOTSTRAP_ENV, "").strip() not in {"1", "true", "yes"}:
            raise RuntimeProvisionError(
                "mise is required to provision a language runtime but was not found "
                f"on PATH. Install mise, or set {BOOTSTRAP_ENV}=1 to let maina run "
                "the mise.run installer (curl | sh)."
            )
        return self._bootstrap_mise(self._cached_mise_path(), base_env)

    def ensure(self, runtime: str, version: str, *, base_env: dict[str, str] | None = None) -> Path:
        """Ensure a mise-managed language runtime is installed; return its bin dir."""
        mise = self.mise_bin(base_env=base_env)
        spec = f"{runtime}@{version}"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        env = self._seed_env(base_env)
        env["MISE_DATA_DIR"] = str(self.data_dir)

        if resolved := self._mise_which(mise, spec, runtime, env):
            return resolved

        run_or_wrap_capped(
            [str(mise), "install", spec],
            env=env,
            timeout=MISE_INSTALL_TIMEOUT,
            max_bytes=INSTALL_OUTPUT_MAX_BYTES,
            error_cls=RuntimeProvisionError,
            on_timeout=lambda: f"mise install {spec} timed out after {MISE_INSTALL_TIMEOUT}s",
            on_failure=lambda detail: f"mise install {spec} failed: {detail}",
        )

        resolved = self._mise_which(mise, spec, runtime, env)
        if resolved is not None:
            return resolved
        raise RuntimeProvisionError(f"could not resolve mise runtime binary for {spec}")

    def env(
        self, runtime: str, version: str, *, base_env: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Environment with mise runtime bin prepended to PATH."""
        bin_dir = self.ensure(runtime, version, base_env=base_env)
        env = self._seed_env(base_env)
        env |= {
            "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
            "MISE_DATA_DIR": str(self.data_dir),
        }
        return env

    def is_present(self, runtime: str, version: str) -> bool:
        """True when ``runtime@version`` already resolves — no install, no bootstrap."""
        return self.present_bin_dir(runtime, version) is not None

    def present_bin_dir(self, runtime: str, version: str) -> Path | None:
        """``runtime@version``'s bin directory when it is *already* installed.

        No install and no bootstrap, so a readiness check (``prepare --check``)
        can compose the PATH a real run would see without provisioning anything.
        ``None`` means "not installed here", never "installing it failed".

        Resolves mise's own path via :meth:`_mise_present` and never calls
        :meth:`mise_bin`, so the ``curl | sh`` bootstrap branch is structurally
        unreachable from this probe — not merely avoided by call order — even
        when ``MAINA_ALLOW_MISE_BOOTSTRAP`` is set in the environment
        (plausible in CI).
        """
        mise = self._mise_present()
        if mise is None:
            return None
        env = os.environ.copy()
        env["MISE_DATA_DIR"] = str(self.data_dir)
        try:
            return self._mise_which(mise, f"{runtime}@{version}", runtime, env)
        except RuntimeProvisionError:
            return None

    @staticmethod
    def _bootstrap_mise(target: Path, base_env: dict[str, str] | None = None) -> Path:
        """Download and install mise into the maina cache."""
        target.parent.mkdir(parents=True, exist_ok=True)
        env = MiseRuntime._seed_env(base_env)
        env |= {"MISE_INSTALL_PATH": str(target), "MISE_VERSION": PINNED_MISE_VERSION}
        curl = shutil.which("curl")
        shell = shutil.which("sh")
        if not curl or not shell:
            raise RuntimeProvisionError("curl and sh are required to bootstrap mise")
        script = run_or_wrap_capped(
            [curl, "-fsSL", "https://mise.run"],
            env=env,
            timeout=MISE_QUERY_TIMEOUT,
            max_bytes=INSTALL_OUTPUT_MAX_BYTES,
            error_cls=RuntimeProvisionError,
            on_timeout=lambda: "mise bootstrap timed out fetching the install script",
            on_failure=lambda detail: f"failed to fetch the mise install script: {detail}",
        )
        run_or_wrap_capped(
            [shell],
            env=env,
            timeout=MISE_INSTALL_TIMEOUT,
            stdin=script.stdout.encode("utf-8"),
            max_bytes=INSTALL_OUTPUT_MAX_BYTES,
            error_cls=RuntimeProvisionError,
            on_timeout=lambda: "mise bootstrap timed out",
            on_failure=lambda detail: f"failed to bootstrap mise: {detail}",
        )
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
        """Resolve *spec*'s binary dir via ``mise which``, falling back to
        ``mise exec`` for a toolchain ``which`` cannot see through.

        Both calls are a *check*, never an install: ``mise exec`` normally
        installs a missing version on demand, which would make this "just
        resolve it" helper (shared by :meth:`ensure`'s own pre-install check
        and the side-effect-free :meth:`present_bin_dir` probe) provision a
        runtime as a side effect of asking whether one is already there.
        ``exec_auto_install=false`` keeps the fallback a pure check; the
        actual install, when wanted, is :meth:`ensure`'s own explicit
        ``mise install`` call.
        """
        try:
            which = run_capped(
                [str(mise), "which", "--toolchain", spec, runtime],
                env=env,
                timeout=MISE_QUERY_TIMEOUT,
                max_bytes=INSTALL_OUTPUT_MAX_BYTES,
            )
            which_stdout = which.stdout.decode("utf-8", "replace")
            if which.returncode == 0 and which_stdout.strip():
                return Path(which_stdout.strip()).parent

            shell = shutil.which("sh") or "/bin/sh"
            no_install_env = {**env, "MISE_EXEC_AUTO_INSTALL": "false"}
            exe = run_capped(
                [str(mise), "exec", spec, "--", shell, "-c", f"command -v {shlex.quote(runtime)}"],
                env=no_install_env,
                timeout=MISE_QUERY_TIMEOUT,
                max_bytes=INSTALL_OUTPUT_MAX_BYTES,
            )
        except OSError as exc:
            raise RuntimeProvisionError(f"could not run mise for {spec}: {exc}") from exc
        except OutputCapExceededError as exc:
            raise RuntimeProvisionError(
                f"mise for {spec} exceeded the {exc.max_bytes} byte cap"
            ) from exc
        exe_stdout = exe.stdout.decode("utf-8", "replace")
        if exe.returncode == 0 and (path := exe_stdout.strip()):
            return Path(path).parent
        return None
