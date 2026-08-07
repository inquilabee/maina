"""Unit tests for registry load/merge/ids."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from pyporter.errors import AbstractToolError, ManifestError
from pyporter.registry import Registry
from pyporter.tool import Tool

if TYPE_CHECKING:
    from pathlib import Path


def test_join_tool_id() -> None:
    assert Registry.join_tool_id("kubectl", "get") == "kubectl/get"
    assert Registry.join_tool_id("git", "remote/add") == "git/remote/add"


def test_load_jq_flat() -> None:
    manifest = Registry.default().load("jq")
    assert manifest.cli == "jq"
    assert not manifest.abstract
    assert manifest.subcommands == []


def test_load_jscpd_family() -> None:
    manifest = Registry.default().load("jscpd")
    assert manifest.cli == "jscpd"
    assert manifest.parser == "json"
    assert manifest.parser_path is None


def test_kubectl_get_uses_general_json() -> None:
    manifest = Registry.default().load("kubectl/get")
    assert manifest.parser == "json"
    assert manifest.parser_path is None


def test_git_remote_add_keeps_family_parser() -> None:
    manifest = Registry.default().load("git/remote/add")
    assert manifest.parser == "add.py"
    assert manifest.parser_path is not None
    assert manifest.parser_path.name == "add.py"


def test_kubectl_get_inheritance() -> None:
    manifest = Registry.default().load("kubectl/get")
    assert manifest.cli == "kubectl"
    assert manifest.subcommands == ["get"]
    assert "namespace" in manifest.args
    assert manifest.install.get("backend") == "path"


def test_git_remote_add_subcommand_chain() -> None:
    registry = Registry.default()
    by_slash = registry.load("git/remote/add")
    by_kw = registry.load(Registry.join_tool_id("git", "remote/add"))
    assert by_slash.subcommands == ["remote", "add"]
    assert by_kw.subcommands == by_slash.subcommands
    tool = Tool("git", subcommand="remote/add")
    assert tool.id == "git/remote/add"


def test_abstract_refuses_run() -> None:
    with pytest.raises(AbstractToolError):
        Tool("kubectl").run(resource="pods")


def test_list_commands() -> None:
    registry = Registry.default()
    tops = registry.list_commands(None)
    assert "jq" in tops
    assert "kubectl" in tops
    children = registry.list_commands("kubectl")
    assert "kubectl/get" in children
    assert "kubectl/apply" in children


def test_extends_cycle(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    (root / "a.yaml").write_text(
        yaml.dump({"cli": "a", "extends": "b"}),
        encoding="utf-8",
    )
    (root / "b.yaml").write_text(
        yaml.dump({"cli": "b", "extends": "a"}),
        encoding="utf-8",
    )
    with pytest.raises(ManifestError, match="cycle"):
        Registry(root).load("a")
