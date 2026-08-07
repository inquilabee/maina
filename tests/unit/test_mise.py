"""MiseRuntime unit tests with fakes (no network / bootstrap)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from pyporter.mise import MiseRuntime

if TYPE_CHECKING:
    from pathlib import Path


def test_mise_env_prepends_ensure_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = MiseRuntime(cache_root=tmp_path)
    bin_dir = tmp_path / "runtimes" / "node" / "bin"
    bin_dir.mkdir(parents=True)
    ensured: list[tuple[str, str]] = []

    def fake_ensure(runtime: str, version: str) -> Path:
        ensured.append((runtime, version))
        return bin_dir

    monkeypatch.setattr(rt, "ensure", fake_ensure)
    monkeypatch.setenv("PATH", "/usr/bin")
    env = rt.env("node", "20.11.0")
    assert ensured == [("node", "20.11.0")]
    path_parts = env["PATH"].split(os.pathsep)
    assert path_parts[0] == str(bin_dir)
    assert env["MISE_DATA_DIR"] == str(tmp_path / "mise-data")
