"""Real network / npm / mise installs. Run with: pytest -m integration.

Every test skips cleanly when its prerequisite is missing, so `-m integration`
is safe to run anywhere; it just does less.
"""

from __future__ import annotations

import shutil
import socket
from pathlib import Path

import pytest

from maina.backends import GithubBackend, NpmBackend, PathBackend
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
        backends=[PathBackend(), GithubBackend(), NpmBackend()],
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
