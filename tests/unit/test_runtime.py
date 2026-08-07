"""Tests for composition Runtime, register, and use."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import maina
from helpers import write_yaml
from maina.core.errors import ManifestError, RegisterError
from maina.core.parsers import ParserResolver
from maina.runtime import Runtime, resolve_cache_root

if TYPE_CHECKING:
    from pathlib import Path


def test_bootstrap_parity() -> None:
    preferred = Runtime.preferred()
    manifest = preferred.registry.load("jq")
    assert manifest.cli == "jq"
    assert preferred.parsers.is_general("json")
    assert preferred.parsers.is_general("raw")


def test_runtime_use_isolates() -> None:
    maina.use(Runtime())
    with pytest.raises(ManifestError):
        maina.Tool("jq")
    maina.use(Runtime.preferred())
    assert maina.Tool("jq").id == "jq"


def test_tool_explicit_runtime(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    write_yaml(root / "local.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = Runtime()
    maina.register(
        rt,
        registry_dirs=[root],
        parsers={"text": ParserResolver.parse_text},
        installers=[],
    )
    # Active preferred still has jq; explicit runtime only sees local.
    assert maina.Tool("jq").id == "jq"
    with pytest.raises(ManifestError):
        maina.Tool("jq", runtime=rt)
    assert maina.Tool("local", runtime=rt).id == "local"


def test_register_missing_registry_dir_errors(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(RegisterError, match="registry"):
        maina.register(Runtime(), registry_dirs=[missing])


def test_register_rejects_positional_and_runtime_kw() -> None:
    rt = Runtime()
    with pytest.raises(ValueError, match="not both"):
        maina.register(rt, runtime=rt)


def test_register_runtime_kw_compat(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    write_yaml(root / "compat.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = Runtime()
    maina.register(registry_dirs=[root], runtime=rt)
    assert rt.registry.load("compat").cli == "echo"


def test_module_register_appends_overlay(tmp_path: Path) -> None:
    root = tmp_path / "overlay"
    write_yaml(
        root / "jq.yaml",
        {"cli": "jq-overlay", "install": {"backend": "path"}, "parser": "text"},
    )
    maina.register(registry_dirs=[root])
    tool = maina.Tool("jq")
    assert tool.id == "jq"
    text = str(tool)
    assert "cli: jq-overlay" in text


def test_resolve_cache_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAINA_CACHE", str(tmp_path / "env-cache"))
    root = resolve_cache_root(None)
    assert root == tmp_path / "env-cache"
    assert root.is_dir()
    explicit = resolve_cache_root(tmp_path / "explicit")
    assert explicit == tmp_path / "explicit"
