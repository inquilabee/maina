"""Real network / npm / mise installs. Run with: pytest -m integration.

Every test skips cleanly when its prerequisite is missing, so `-m integration`
is safe to run anywhere; it just does less.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from maina.backends import GithubBackend, NpmBackend, PathBackend, PipBackend
from maina.core.errors import InstallError
from maina.core.registry import InstallSpec, Manifest, Registry
from maina.installer import Installer
from maina.mise import MiseRuntime
from maina.runner import ToolRunner

pytestmark = pytest.mark.integration


def online() -> bool:
    try:
        socket.create_connection(("github.com", 443), timeout=5).close()
    except OSError:
        return False
    return True


def make_installer(tmp_path: Path) -> Installer:
    cache = tmp_path / "cache"
    return Installer(
        cache_root=cache,
        registry=Registry([]),
        language=MiseRuntime(cache_root=cache),
        backends=[PathBackend(), GithubBackend(), NpmBackend(), PipBackend()],
    )


def test_github_download_and_run_jq(tmp_path: Path) -> None:
    if not online():
        pytest.skip("offline")
    manifest = Manifest(
        id="jq",
        data={"cli": "jq", "version": "1.7.1"},
        install=InstallSpec(
            backend="github",
            repo="jqlang/jq",
            binary="jq",
            tag="jq-1.7.1",
            assets={
                "linux-x86_64": "jq-linux-amd64",
                "linux-arm64": "jq-linux-arm64",
                "macos-x86_64": "jq-macos-amd64",
                "macos-arm64": "jq-macos-arm64",
            },
        ),
    )
    inst = make_installer(tmp_path)
    exe, _env = inst.resolve(manifest)
    assert Path(exe).is_file()
    result = ToolRunner(manifest, installer=inst).run({}, extra=["-n", "1+1"])
    assert result.ok
    assert result.raw.strip() == "2"
    # Second resolve is a cache hit — no download.
    exe2, _ = inst.resolve(manifest)
    assert exe2 == exe


def test_npm_install_and_run(tmp_path: Path) -> None:
    if shutil.which("npm") is None:
        pytest.skip("npm not on PATH")
    manifest = Manifest(
        id="cowsay",
        data={"cli": "cowsay", "version": "1.6.0"},
        install=InstallSpec(backend="npm", package="cowsay"),
    )
    inst = make_installer(tmp_path)
    exe, _env = inst.resolve(manifest)
    resolved = Path(exe).resolve()
    assert (tmp_path / "cache") in resolved.parents  # installed into the maina cache
    result = ToolRunner(manifest, installer=inst).run({}, extra=["moo"])
    assert result.ok
    assert "moo" in result.raw


def test_mise_ensure_node(tmp_path: Path) -> None:
    import os

    if not os.environ.get("MAINA_ALLOW_MISE_BOOTSTRAP") and shutil.which("mise") is None:
        pytest.skip("mise not available and bootstrap not allowed")
    mise = MiseRuntime(cache_root=tmp_path / "cache")
    bin_dir = mise.ensure("node", "lts")
    assert (bin_dir / "node").exists() or (bin_dir / "node.exe").exists()


def test_pip_installs_into_an_isolated_per_tool_venv(tmp_path: Path) -> None:
    """Two pip tools must not share a dependency graph — the shared-venv layout
    they replace lets any two co-installed tools conflict."""
    if not online():
        pytest.skip("offline")
    installer = make_installer(tmp_path)
    installed: dict[str, Path] = {}
    for cli, package, version in (
        ("yamllint", "yamllint", "1.38.0"),
        ("codespell", "codespell", "2.4.3"),
    ):
        manifest = Manifest(
            id=cli,
            data={"cli": cli, "version": version},
            install=InstallSpec(backend="pip", package=package),
        )
        exe, _env = installer.resolve(manifest)
        installed[cli] = Path(exe)
        assert installed[cli].is_file()

    venvs = {cli: path.parent.parent for cli, path in installed.items()}
    assert venvs["yamllint"] != venvs["codespell"]
    for cli, venv in venvs.items():
        assert venv.is_relative_to(tmp_path)
        # Each venv holds its own tool and not the other one.
        other = "codespell" if cli == "yamllint" else "yamllint"
        assert (venv / "bin" / cli).is_file()
        assert not (venv / "bin" / other).exists()

    proc = subprocess.run(
        [str(installed["yamllint"]), "--version"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    assert "1.38.0" in proc.stdout


def test_pip_refuses_a_package_with_no_wheel_unless_allowed(tmp_path: Path) -> None:
    """``--only-binary=:all:`` is the default because building an sdist runs the
    package's own setup.py. The refusal must be a clean InstallError."""
    if not online():
        pytest.skip("offline")
    installer = make_installer(tmp_path)
    # `ssh-import-id` ships sdist-only on PyPI.
    manifest = Manifest(
        id="sdist-only",
        data={"cli": "ssh-import-id", "version": "5.11"},
        install=InstallSpec(backend="pip", package="ssh-import-id"),
    )
    with pytest.raises(InstallError):
        installer.resolve(manifest)
