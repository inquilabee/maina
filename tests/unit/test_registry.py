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


def test_cli_less_base_is_not_a_callable_id(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(root / "base.yaml", {"env": {"LC_ALL": "C"}})  # no cli:, no abstract:
    write_yaml(root / "greet.yaml", {"cli": "echo", "install": {"backend": "path"}})
    registry = Registry(root)
    assert registry.list_commands(None) == ["greet"]
    assert not registry.load("base").has_cli


def test_list_commands_finds_children_under_a_hyphenated_directory(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    write_yaml(root / "my-tool" / "my-tool.yaml", {"cli": "my-tool", "abstract": True})
    write_yaml(
        root / "my-tool" / "sub.yaml",
        {"cli": "my-tool", "subcommand": "sub", "install": {"backend": "path"}},
    )
    registry = Registry(root)
    assert registry.list_commands("my-tool") == ["my-tool-sub"]


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


def test_install_assets_deep_merge_across_extends(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {
            "abstract": True,
            "install": {
                "backend": "github",
                "repo": "a/b",
                "assets": {"linux-x86_64": "t-linux", "macos-arm64": "t-mac"},
            },
        },
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "t",
            "extends": "base",
            "install": {"assets": {"linux-x86_64": "t-linux-v2"}},
        },
    )
    inst = Registry(root).load("child").install
    assert inst.assets == {"linux-x86_64": "t-linux-v2", "macos-arm64": "t-mac"}


def test_required_arg_is_not_satisfied_by_none() -> None:
    from maina.core.argparser import ArgParser
    from maina.core.errors import ArgError

    schema = {"x": {"positional": True, "required": True, "default": None}}
    with pytest.raises(ArgError, match="missing required argument: x"):
        ArgParser(schema).build("echo", [], {})
    with pytest.raises(ArgError, match="missing required argument: x"):
        ArgParser(schema).build("echo", [], {"x": None})


def test_child_can_drop_an_inherited_arg_with_null(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "args": {"legacy": {"flag": "--legacy"}, "keep": {"flag": "--keep"}}},
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "echo",
            "extends": "base",
            "install": {"backend": "path"},
            "args": {"legacy": None},
        },
    )
    manifest = Registry(root).load("child")
    assert "legacy" not in manifest.args
    assert "keep" in manifest.args


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


@pytest.mark.parametrize("line", ["on: push", "yes: 1", "42: x"])
def test_non_string_yaml_key_is_a_clean_manifest_error(tmp_path: Path, line: str) -> None:
    # YAML 1.1 turns `on:` / `yes:` into booleans; the loader must not TypeError.
    root = tmp_path / "reg"
    root.mkdir()
    (root / "t.yaml").write_text(f"cli: echo\n{line}\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="unknown top-level keys"):
        Registry(root).load("t")


def test_yaml_boolean_key_does_not_hide_the_whole_catalog(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "good", "install": {"backend": "path"}})
    (root / "bad.yaml").write_text("cli: bad\non: push\n", encoding="utf-8")
    with pytest.warns(RuntimeWarning):
        assert Registry(root).list_commands() == ["good"]


def test_registry_scan_ignores_dot_directories(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "ok.yaml", {"cli": "echo", "install": {"backend": "path"}})
    wf = root / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yaml").write_text("name: ci\non: push\n", encoding="utf-8")
    reg = Registry(root)
    assert reg.list_commands() == ["ok"]
    with pytest.raises(ManifestError, match="no registry entry"):
        reg.load("github-workflows-ci")


def test_yaml_anchor_flood_rejected(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    root.mkdir()
    anchors = ",".join(f"&a{i} 1" for i in range(200))
    (root / "t.yaml").write_text(
        f"cli: t\ninstall: {{backend: path}}\nx: [{anchors}]\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="anchors"):
        Registry(root).load("t")


def test_base_yaml_is_scoped_to_its_own_root(tmp_path: Path) -> None:
    mine = tmp_path / "mine"
    write_yaml(mine / "mytool.yaml", {"cli": "echo", "install": {"backend": "path"}})
    vendor = tmp_path / "vendor"
    write_yaml(vendor / "base.yaml", {"abstract": True, "env": {"VENDOR": "1"}})
    write_yaml(vendor / "theirs.yaml", {"cli": "echo", "install": {"backend": "path"}})
    reg = Registry([mine, vendor])
    # A base.yaml in the vendor root must not leak into my root's tool.
    assert reg.load("mytool").env == {}
    # …but the vendor's own tool still picks it up.
    assert reg.load("theirs").env == {"VENDOR": "1"}


def test_base_yaml_extends_a_tool_in_the_same_root(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "base.yaml", {"abstract": True, "args": {"shared": {"flag": "--shared"}}})
    write_yaml(root / "leaf.yaml", {"cli": "leaf", "install": {"backend": "path"}})
    assert "shared" in Registry(root).load("leaf").args


def test_separator_and_order_are_mutually_exclusive(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {
            "cli": "t",
            "install": {"backend": "path"},
            "separator": "--",
            "args": {"p": {"positional": True, "order": 1}},
        },
    )
    with pytest.raises(ManifestError, match="two ways to lay out argv"):
        Registry(root).load("t")


def test_doubled_stem_manifest_is_loadable(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "docker-docker.yaml", {"cli": "docker", "install": {"backend": "path"}})
    reg = Registry(root)
    assert reg.list_commands() == ["docker"]
    assert reg.load("docker").cli == "docker"


def test_platforms_scalar_rejected_at_load(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "t", "platforms": "linux", "install": {"backend": "path"}})
    with pytest.raises(ManifestError, match=r"platforms .* must be a list"):
        Registry(root).load("t")


@pytest.mark.parametrize(
    ("field", "yaml"),
    [
        ("version", "cli: t\nversion: 3.10\ninstall: {backend: path}\n"),
        ("cli", "cli: yes\ninstall: {backend: path}\n"),
        ("install.tag", "cli: t\ninstall: {backend: github, repo: a/b, tag: on}\n"),
        ("env.DEBUG", "cli: t\ninstall: {backend: path}\nenv: {DEBUG: yes}\n"),
    ],
)
def test_unquoted_yaml_scalar_rejected(tmp_path: Path, field: str, yaml: str) -> None:
    del field
    root = tmp_path / "reg"
    root.mkdir()
    (root / "t.yaml").write_text(yaml, encoding="utf-8")
    with pytest.raises(ManifestError, match="quoted string"):
        Registry(root).load("t")


@pytest.mark.parametrize(
    "yaml",
    [
        'cli: t\nabstract: "true"\ninstall: {backend: path}\n',
        "cli: t\ninstall: {backend: path}\nargs: {p: {positional: 1}}\n",
        'cli: t\ninstall: {backend: path, package: p, allow_scripts: "yes"}\n',
    ],
)
def test_string_valued_boolean_field_rejected(tmp_path: Path, yaml: str) -> None:
    root = tmp_path / "reg"
    root.mkdir()
    (root / "t.yaml").write_text(yaml, encoding="utf-8")

    def load_and_touch() -> None:
        m = Registry(root).load("t")
        _ = (m.abstract, m.args, m.install)  # some checks fire lazily on access

    with pytest.raises(ManifestError, match="true or false"):
        load_and_touch()


@pytest.mark.parametrize("bad", ["-rf", "*", "a?b", "x[0]"])
def test_cli_name_rejects_flag_and_glob_chars(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": bad, "install": {"backend": "path"}})
    with pytest.raises(ManifestError, match="not a safe path segment"):
        Registry(root).load("t").cli  # ruff:ignore[useless-expression] - property triggers validation


def test_install_sha256_must_be_64_hex(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {
            "cli": "t",
            "install": {
                "backend": "github",
                "repo": "acme/t",
                "assets": {"linux-x86_64": "t.tgz"},
                "sha256": {"linux-x86_64": "abc123"},
            },
        },
    )
    with pytest.raises(ManifestError, match="64-char hex"):
        Registry(root).load("t")


@pytest.mark.parametrize("bad", [0, -5])
def test_timeout_non_positive_rejected_at_load(tmp_path: Path, bad: int) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "t", "install": {"backend": "path"}, "timeout": bad})
    with pytest.raises(ManifestError, match="timeout"):
        Registry(root).load("t")


def test_list_commands_skips_broken_manifest_with_warning(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "good", "install": {"backend": "path"}})
    (root / "broken.yaml").write_text("cli: broken\noutputs: {}\n", encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="broken"):
        ids = Registry(root).list_commands()
    assert ids == ["good"]


def test_inherited_parser_resolves_against_declaring_family(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "famA" / "famA.yaml", {"abstract": True, "parser": "p.py"})
    (root / "famA" / "parsers").mkdir(parents=True)
    (root / "famA" / "parsers" / "p.py").write_text(
        "def parse(raw, result):\n    return raw.upper()\n", encoding="utf-8"
    )
    write_yaml(
        root / "famB" / "child.yaml",
        {"cli": "echo", "extends": "famA", "install": {"backend": "path"}},
    )
    manifest = Registry(root).load("famB-child")
    assert manifest.parser == "p.py"
    assert manifest.parser_path is not None
    assert manifest.parser_path.parent == root / "famA" / "parsers"


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
