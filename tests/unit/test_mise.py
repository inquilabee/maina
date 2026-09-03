"""MiseRuntime unit tests with fakes (no network / bootstrap)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from maina.mise import MiseRuntime

if TYPE_CHECKING:
    from pathlib import Path


def test_mise_env_prepends_ensure_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = MiseRuntime(cache_root=tmp_path)
    bin_dir = tmp_path / "runtimes" / "node" / "bin"
    bin_dir.mkdir(parents=True)
    ensured: list[tuple[str, str]] = []

    def fake_ensure(runtime: str, version: str, **_kw: object) -> Path:
        ensured.append((runtime, version))
        return bin_dir

    monkeypatch.setattr(rt, "ensure", fake_ensure)
    monkeypatch.setenv("PATH", "/usr/bin")
    env = rt.env("node", "20.11.0")
    assert ensured == [("node", "20.11.0")]
    path_parts = env["PATH"].split(os.pathsep)
    assert path_parts[0] == str(bin_dir)
    assert env["MISE_DATA_DIR"] == str(tmp_path / "mise-data")


def test_mise_bin_bootstrap_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maina.core.errors import RuntimeProvisionError
    from maina.mise import MiseRuntime

    monkeypatch.setattr("maina.mise.shutil.which", lambda *_a, **_k: None)
    monkeypatch.delenv("MAINA_ALLOW_MISE_BOOTSTRAP", raising=False)
    rt = MiseRuntime(cache_root=tmp_path / "cache")
    with pytest.raises(RuntimeProvisionError, match="MAINA_ALLOW_MISE_BOOTSTRAP"):
        rt.mise_bin()
