"""Tests for composition Runtime, register, and use."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

import pyporter
from pyporter.errors import ManifestError
from pyporter.parsers import ParserResolver
from pyporter.runtime import Runtime

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
    root.mkdir()
    (root / "local.yaml").write_text(
        yaml.dump({"cli": "echo", "install": {"backend": "path"}}),
        encoding="utf-8",
    )
    rt = Runtime()
    rt.register(
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
    with pytest.raises(FileNotFoundError, match="registry"):
        pyporter.register(registry_dirs=[missing], runtime=Runtime())


def test_module_register_appends_overlay(tmp_path: Path) -> None:
    root = tmp_path / "overlay"
    root.mkdir()
    (root / "jq.yaml").write_text(
        yaml.dump({"cli": "jq-overlay", "install": {"backend": "path"}, "parser": "text"}),
        encoding="utf-8",
    )
    pyporter.register(registry_dirs=[root])
    tool = pyporter.Tool("jq")
    assert tool.id == "jq"
    text = str(tool)
    assert "cli: jq-overlay" in text
