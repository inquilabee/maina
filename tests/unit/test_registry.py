"""Unit tests for registry load/merge/ids."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from helpers import fixture_registry, write_yaml
from maina.core.errors import AbstractToolError, ManifestError
from maina.core.registry import Registry
from maina.tool import Tool

if TYPE_CHECKING:
    from pathlib import Path


def test_join_tool_id() -> None:
    assert Registry.join_tool_id("kubectl", "get") == "kubectl-get"
    assert Registry.join_tool_id("git", "remote/add") == "git-remote-add"
    assert Registry.join_tool_id("git", "remote-add") == "git-remote-add"


def test_load_jq_flat() -> None:
    manifest = fixture_registry().load("jq")
    assert manifest.cli == "jq"
    assert not manifest.abstract
    assert manifest.subcommands == []


def test_load_jscpd_family() -> None:
    manifest = fixture_registry().load("jscpd")
    assert manifest.cli == "jscpd"
    assert manifest.parser == "json"
    assert manifest.parser_path is None


def test_kubectl_get_uses_general_json() -> None:
    manifest = fixture_registry().load("kubectl-get")
    assert manifest.parser == "json"
    assert manifest.parser_path is None


def test_git_remote_add_keeps_family_parser() -> None:
    manifest = fixture_registry().load("git-remote-add")
    assert manifest.parser == "add.py"
    assert manifest.parser_path is not None
    assert manifest.parser_path.name == "add.py"


def test_kubectl_get_inheritance() -> None:
    manifest = fixture_registry().load("kubectl-get")
    assert manifest.cli == "kubectl"
    assert manifest.subcommands == ["get"]
    assert "namespace" in manifest.args
    assert manifest.install.backend == "path"


def test_git_remote_add_subcommand_chain() -> None:
    registry = fixture_registry()
    by_id = registry.load("git-remote-add")
    by_kw = registry.load(Registry.join_tool_id("git", "remote-add"))
    assert by_id.subcommands == ["remote", "add"]
    assert by_kw.subcommands == by_id.subcommands
    tool = Tool("git", subcommand="remote-add")
    assert tool.id == "git-remote-add"


def test_abstract_refuses_run() -> None:
    with pytest.raises(AbstractToolError):
        Tool("kubectl").run(resource="pods")


def test_list_commands() -> None:
    registry = fixture_registry()
    tops = registry.list_commands(None)
    assert "jq" in tops
    assert "base" not in tops
    assert "kubectl" not in tops
    assert "git" not in tops
    assert "kubectl-get" in tops
    assert "kubectl-apply" in tops
    assert "git-remote-add" in tops
    children = registry.list_commands("kubectl")
    assert "kubectl-get" in children
    assert "kubectl-apply" in children
    assert "kubectl" not in children


def test_list_commands_excludes_abstract(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(root / "base.yaml", {"abstract": True, "cli": "base"})
    write_yaml(root / "leaf.yaml", {"cli": "leaf", "install": {"backend": "path"}})
    write_yaml(
        root / "family" / "family.yaml",
        {"cli": "family", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "family" / "child.yaml",
        {
            "extends": "family",
            "subcommand": "child",
            "abstract": False,
            "install": {"backend": "path"},
        },
    )
    registry = Registry(root)
    tops = registry.list_commands(None)
    assert "leaf" in tops
    assert "family-child" in tops
    assert "base" not in tops
    assert "family" not in tops
    assert registry.list_commands("family") == ["family-child"]


def test_tool_id_rejects_dotdot_traversal(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "ok.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(
        tmp_path / "sibling" / "secret.yaml",
        {"cli": "echo", "install": {"backend": "path"}},
    )
    registry = Registry(root)
    for bad in ("-..-sibling-secret", "..-sibling-secret", ".-ok"):
        with pytest.raises(ManifestError, match="unsafe tool id"):
            registry.load(bad)


def test_extends_cycle(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(root / "a.yaml", {"cli": "a", "extends": "b"})
    write_yaml(root / "b.yaml", {"cli": "b", "extends": "a"})
    with pytest.raises(ManifestError, match="cycle"):
        Registry(root).load("a")


@pytest.mark.parametrize("evil", ["/etc/evil.py", "../evil.py", "sub/../../evil.py"])
def test_parser_ref_outside_parsers_dir_rejected(tmp_path: Path, evil: str) -> None:
    """A ``parser:`` ref is exec'd, so it must not escape the family parsers/ dir."""
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "t", "install": {"backend": "path"}, "parser": evil})
    (root / "parsers").mkdir(parents=True)
    (root / "parsers" / "ok.py").write_text("def parse(raw, result):\n    return raw\n")
    with pytest.raises(ManifestError, match="parser"):
        Registry(root).load("t")


def test_list_commands_skips_broken_manifest_with_warning(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "good", "install": {"backend": "path"}})
    (root / "broken.yaml").write_text("cli: broken\noutputs: {}\n", encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="broken"):
        ids = Registry(root).list_commands()
    assert ids == ["good"]


def test_parser_ref_plain_filename_resolves(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "t", "install": {"backend": "path"}, "parser": "ok.py"})
    (root / "parsers").mkdir(parents=True)
    (root / "parsers" / "ok.py").write_text("def parse(raw, result):\n    return raw\n")
    manifest = Registry(root).load("t")
    assert manifest.parser_path is not None
    assert manifest.parser_path.name == "ok.py"


def test_base_auto_extends_when_present(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(root / "demo.yaml", {"cli": "demo", "install": {"backend": "path"}})
    manifest = Registry(root).load("demo")
    assert manifest.requires.runtimes == {"python": "3.14", "node": "latest"}
    assert not manifest.abstract


def test_base_does_not_auto_extend_itself(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    base = Registry(root).load("base")
    assert base.abstract
    assert base.requires.runtimes == {"python": "3.14", "node": "latest"}


def test_extends_parent_also_gets_base(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(
        root / "parent.yaml",
        {"cli": "parent", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "child.yaml",
        {"extends": "parent", "subcommand": "child", "abstract": False},
    )
    child = Registry(root).load("child")
    assert child.requires.runtimes == {"python": "3.14", "node": "latest"}
    assert child.cli == "parent"


def test_requires_map_override_merges(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(
        root / "demo.yaml",
        {
            "cli": "demo",
            "requires": {"node": "22", "ruby": "3.3"},
            "install": {"backend": "path"},
        },
    )
    manifest = Registry(root).load("demo")
    assert manifest.requires.runtimes == {
        "python": "3.14",
        "node": "22",
        "ruby": "3.3",
    }
    assert manifest.requires.get("node") == "22"
    assert manifest.requires.get("python") == "3.14"


def test_requires_legacy_form(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "demo.yaml",
        {
            "cli": "demo",
            "requires": {"runtime": "node", "version": "20"},
            "install": {"backend": "path"},
        },
    )
    manifest = Registry(root).load("demo")
    assert manifest.requires.runtimes == {"node": "20"}


def test_requires_legacy_merges_with_base(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(
        root / "demo.yaml",
        {
            "cli": "demo",
            "requires": {"runtime": "node", "version": "20"},
            "install": {"backend": "path"},
        },
    )
    manifest = Registry(root).load("demo")
    assert manifest.requires.runtimes == {"python": "3.14", "node": "20"}


def test_requires_rejects_mixed_legacy_and_pins(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "demo.yaml",
        {
            "cli": "demo",
            "requires": {"runtime": "node", "python": "3.14"},
            "install": {"backend": "path"},
        },
    )
    with pytest.raises(ManifestError, match="cannot mix"):
        Registry(root).load("demo")


def test_shipped_tools_carry_no_surprise_runtime_pins() -> None:
    # Shipped base.yaml has no runtime pins: a static binary must not drag mise.
    registry = fixture_registry()
    assert registry.load("jq").requires.runtimes == {}
    assert registry.load("shellcheck").requires.runtimes == {}
    # npm tools may still declare node explicitly where they need it.
    jscpd = registry.load("jscpd")
    assert jscpd.requires.runtimes == {} or jscpd.requires.get("node") is not None


def test_requires_rejects_yaml_float_pins(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir(parents=True)
    # Unquoted 3.10 / 3.14 become YAML floats (3.1 / 3.14) — must fail closed.
    (root / "bad310.yaml").write_text(
        "cli: bad310\nrequires:\n  python: 3.10\ninstall:\n  backend: path\n",
        encoding="utf-8",
    )
    (root / "bad314.yaml").write_text(
        "cli: bad314\nrequires:\n  python: 3.14\ninstall:\n  backend: path\n",
        encoding="utf-8",
    )
    registry = Registry(root)
    with pytest.raises(ManifestError, match="quoted version"):
        registry.load("bad310")
    with pytest.raises(ManifestError, match="quoted version"):
        registry.load("bad314")


def test_requires_null_clears_inherited(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(
        root / "demo.yaml",
        {"cli": "demo", "requires": None, "install": {"backend": "path"}},
    )
    manifest = Registry(root).load("demo")
    assert manifest.requires.runtimes == {}


def test_requires_null_pin_drops_one(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(
        root / "demo.yaml",
        {
            "cli": "demo",
            "requires": {"node": None},
            "install": {"backend": "path"},
        },
    )
    manifest = Registry(root).load("demo")
    assert manifest.requires.runtimes == {"python": "3.14"}


def test_canonical_family_extends() -> None:
    registry = fixture_registry()
    kubectl_get = registry.load("kubectl-get")
    assert kubectl_get.cli == "kubectl"
    assert kubectl_get.subcommands == ["get"]
    git_remote = registry.load("git-remote")
    assert git_remote.cli == "git"
    assert git_remote.subcommands == ["remote"]
    git_remote_add = registry.load("git-remote-add")
    assert git_remote_add.subcommands == ["remote", "add"]
    # Legacy name-name spellings alias to the canonical family id.
    assert registry.load("kubectl-kubectl").id == "kubectl"
    assert registry.load("git-git").id == "git"
    assert registry.load("git-remote-remote").id == "git-remote"
