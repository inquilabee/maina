"""Unit tests for registry load/merge/ids."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyporter.errors import AbstractToolError, ManifestError
from pyporter.registry import Registry
from pyporter.runtime import Runtime
from pyporter.tool import Tool

from helpers import write_yaml

if TYPE_CHECKING:
    from pathlib import Path


def preferred_registry() -> Registry:
    return Runtime.preferred().registry


def test_join_tool_id() -> None:
    assert Registry.join_tool_id("kubectl", "get") == "kubectl/get"
    assert Registry.join_tool_id("git", "remote/add") == "git/remote/add"


def test_load_jq_flat() -> None:
    manifest = preferred_registry().load("jq")
    assert manifest.cli == "jq"
    assert not manifest.abstract
    assert manifest.subcommands == []


def test_load_jscpd_family() -> None:
    manifest = preferred_registry().load("jscpd")
    assert manifest.cli == "jscpd"
    assert manifest.parser == "json"
    assert manifest.parser_path is None


def test_kubectl_get_uses_general_json() -> None:
    manifest = preferred_registry().load("kubectl/get")
    assert manifest.parser == "json"
    assert manifest.parser_path is None


def test_git_remote_add_keeps_family_parser() -> None:
    manifest = preferred_registry().load("git/remote/add")
    assert manifest.parser == "add.py"
    assert manifest.parser_path is not None
    assert manifest.parser_path.name == "add.py"


def test_kubectl_get_inheritance() -> None:
    manifest = preferred_registry().load("kubectl/get")
    assert manifest.cli == "kubectl"
    assert manifest.subcommands == ["get"]
    assert "namespace" in manifest.args
    assert manifest.install.backend == "path"


def test_git_remote_add_subcommand_chain() -> None:
    registry = preferred_registry()
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
    registry = preferred_registry()
    tops = registry.list_commands(None)
    assert "jq" in tops
    assert "kubectl" in tops
    children = registry.list_commands("kubectl")
    assert "kubectl/get" in children
    assert "kubectl/apply" in children


def test_extends_cycle(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(root / "a.yaml", {"cli": "a", "extends": "b"})
    write_yaml(root / "b.yaml", {"cli": "b", "extends": "a"})
    with pytest.raises(ManifestError, match="cycle"):
        Registry(root).load("a")
