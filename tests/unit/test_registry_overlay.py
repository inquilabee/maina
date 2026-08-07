"""Tests for multi-root registry overlays."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.core.errors import ManifestError
from maina.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path


def test_overlay_later_root_wins(tmp_path: Path) -> None:
    early = tmp_path / "early"
    late = tmp_path / "late"
    write_yaml(early / "demo.yaml", {"cli": "early", "parser": "text", "version": "1"})
    write_yaml(late / "demo.yaml", {"cli": "late", "parser": "json", "version": "2"})
    registry = Registry([early, late])
    manifest = registry.load("demo")
    assert manifest.cli == "late"
    assert manifest.parser == "json"
    assert manifest.version == "2"


def test_overlay_extends_parent_in_other_root(tmp_path: Path) -> None:
    shipped = tmp_path / "shipped"
    project = tmp_path / "project"
    write_yaml(
        shipped / "base.yaml",
        {
            "cli": "basecli",
            "abstract": True,
            "install": {"backend": "path"},
            "args": {"a": {"flag": "-a"}},
        },
    )
    write_yaml(
        project / "child.yaml",
        {"extends": "base", "subcommand": "child", "args": {"b": {"flag": "-b"}}},
    )
    registry = Registry([shipped, project])
    manifest = registry.load("child")
    assert manifest.cli == "basecli"
    assert manifest.subcommands == ["child"]
    assert "a" in manifest.args
    assert "b" in manifest.args
    assert manifest.install.backend == "path"


def test_overlay_list_commands_union(tmp_path: Path) -> None:
    early = tmp_path / "early"
    late = tmp_path / "late"
    write_yaml(early / "alpha.yaml", {"cli": "alpha"})
    write_yaml(early / "shared.yaml", {"cli": "early-shared"})
    write_yaml(late / "beta.yaml", {"cli": "beta"})
    write_yaml(late / "shared.yaml", {"cli": "late-shared"})
    registry = Registry([early, late])
    tops = registry.list_commands(None)
    assert tops.count("shared") == 1
    assert "alpha" in tops
    assert "beta" in tops
    assert registry.load("shared").cli == "late-shared"


def test_overlay_family_parser_uses_winning_leaf_root(tmp_path: Path) -> None:
    early = tmp_path / "early"
    late = tmp_path / "late"
    write_yaml(early / "fam" / "fam.yaml", {"cli": "fam", "parser": "leaf.py"})
    (early / "fam" / "parsers").mkdir(parents=True)
    (early / "fam" / "parsers" / "leaf.py").write_text(
        "def parse(raw, result):\n    return 'early'\n",
        encoding="utf-8",
    )
    write_yaml(late / "fam" / "fam.yaml", {"cli": "fam", "parser": "leaf.py"})
    (late / "fam" / "parsers").mkdir(parents=True)
    (late / "fam" / "parsers" / "leaf.py").write_text(
        "def parse(raw, result):\n    return 'late'\n",
        encoding="utf-8",
    )
    manifest = Registry([early, late]).load("fam")
    assert manifest.parser_path is not None
    assert manifest.parser_path.read_text(encoding="utf-8").count("late") == 1


def test_single_root_constructor_still_works(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(root / "only.yaml", {"cli": "only"})
    registry = Registry(root)
    assert registry.root == root
    assert registry.load("only").cli == "only"


def test_empty_registry_load_errors() -> None:
    with pytest.raises(ManifestError, match="no roots"):
        Registry([]).load("jq")
