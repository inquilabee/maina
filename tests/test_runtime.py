"""Tests for composition Runtime, register, and use."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import pyporter
from pyporter.errors import ManifestError, RegisterError
from pyporter.parsers import ParserResolver
from pyporter.runtime import Runtime, resolve_cache_root

from helpers import write_yaml

if TYPE_CHECKING:
    from pathlib import Path


def test_bootstrap_parity() -> None:
    preferred = Runtime.preferred()
    manifest = preferred.registry.load("jq")
    assert manifest.cli == "jq"
    assert preferred.parsers.is_general("json")
    assert preferred.parsers.is_general("raw")


def test_runtime_use_isolates() -> None:
    pyporter.use(Runtime())
    with pytest.raises(ManifestError):
        pyporter.Tool("jq")
    pyporter.use(Runtime.preferred())
    assert pyporter.Tool("jq").id == "jq"


def test_tool_explicit_runtime(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    write_yaml(root / "local.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = Runtime()
    pyporter.register(
        rt,
        registry_dirs=[root],
        parsers={"text": ParserResolver.parse_text},
        installers=[],
    )
    # Active preferred still has jq; explicit runtime only sees local.
    assert pyporter.Tool("jq").id == "jq"
    with pytest.raises(ManifestError):
        pyporter.Tool("jq", runtime=rt)
    assert pyporter.Tool("local", runtime=rt).id == "local"


def test_register_missing_registry_dir_errors(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(RegisterError, match="registry"):
        pyporter.register(Runtime(), registry_dirs=[missing])


def test_register_rejects_positional_and_runtime_kw() -> None:
    rt = Runtime()
    with pytest.raises(ValueError, match="not both"):
        pyporter.register(rt, runtime=rt)


def test_register_runtime_kw_compat(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    write_yaml(root / "compat.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = Runtime()
    pyporter.register(registry_dirs=[root], runtime=rt)
    assert rt.registry.load("compat").cli == "echo"


def test_module_register_appends_overlay(tmp_path: Path) -> None:
    root = tmp_path / "overlay"
    write_yaml(
        root / "jq.yaml",
        {"cli": "jq-overlay", "install": {"backend": "path"}, "parser": "text"},
    )
    pyporter.register(registry_dirs=[root])
    tool = pyporter.Tool("jq")
    assert tool.id == "jq"
    text = str(tool)
    assert "cli: jq-overlay" in text


def test_resolve_cache_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYPORTER_CACHE", str(tmp_path / "env-cache"))
    root = resolve_cache_root(None)
    assert root == tmp_path / "env-cache"
    assert root.is_dir()
    explicit = resolve_cache_root(tmp_path / "explicit")
    assert explicit == tmp_path / "explicit"
