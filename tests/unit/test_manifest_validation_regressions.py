"""Regression tests: install identity hashing, manifest field validation, registry diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from helpers import write_yaml
from maina.core.errors import ManifestError
from maina.core.registry import InstallSpec, Registry
from maina.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


def make_raw_reg(tmp_path: Path, files: dict[str, str]) -> Registry:
    """Like :func:`make_reg`, but each value is literal YAML text.

    Needed when the test cares about the *source spelling* of a bare
    scalar (``on:`` vs ``yes:`` both resolve to the same Python ``True`` key,
    which a Python-dict fixture can only ever represent as one key already).
    """
    root = tmp_path / "reg"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Registry(root)


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


# --- install identity hashing + manifest field validation ----------------------


def test_cache_version_covers_requires_for_npm(tmp_path: Path) -> None:
    from maina.installer import Installer

    base = {"cli": "jscpd", "version": "4.0.5", "install": {"backend": "npm", "package": "jscpd"}}
    reg = make_reg(
        tmp_path,
        {
            "a.yaml": {**base, "requires": {"node": "18"}},
            "b.yaml": {**base, "requires": {"node": "22"}},
        },
    )
    assert Installer._cache_version(reg.load("a")) != Installer._cache_version(reg.load("b"))


def test_identity_key_covers_binary_and_allow_scripts() -> None:
    base = InstallSpec(backend="github", repo="acme/x", tag="v1", assets={"k": "x.tgz"})
    other_binary = InstallSpec(
        backend="github", repo="acme/x", tag="v1", assets={"k": "x.tgz"}, binary="x"
    )
    scripts_on = InstallSpec(backend="npm", package="x", allow_scripts=True)
    scripts_off = InstallSpec(backend="npm", package="x")
    assert base.identity_key() != other_binary.identity_key()
    assert scripts_on.identity_key() != scripts_off.identity_key()


def test_install_package_name_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "npm", "package": "evil; rm -rf /"},
            }
        },
    )
    with pytest.raises(ManifestError, match="not a valid npm name"):
        reg.load("t")


def test_install_scoped_package_name_accepted(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "npm", "package": "@scope/pkg.js"},
            }
        },
    )
    assert reg.load("t").install.package == "@scope/pkg.js"


def test_install_tag_traversal_rejected_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "github", "repo": "acme/t", "tag": "../../etc"},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"install\.tag"):
        reg.load("t")


def test_requires_key_charset_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "path"},
                "requires": {"no de;x": "20"},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"\[A-Za-z0-9_\.\+-\]"):
        reg.load("t")


# --- MAINA_REGISTRY diagnostics ---------------------------------------------


def test_env_registry_missing_dir_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAINA_REGISTRY", str(tmp_path / "does-not-exist"))
    with pytest.warns(RuntimeWarning, match="not a directory"):
        Runtime._env_registry_dirs()


# --- args.* scalar guards --------------------------------------------------------


@pytest.mark.parametrize("literal", ["no", "on", "3.10"])
def test_arg_default_rejects_bare_yaml_scalar(tmp_path: Path, literal: str) -> None:
    root = tmp_path / "reg"
    root.mkdir()
    # Raw YAML so the 1.1 bool/float trap is actually exercised (a quoted value
    # would round-trip as a string).
    body = "cli: t\ninstall: {backend: path}\nargs:\n"
    body += f"  color: {{flag: --color, default: {literal}}}\n"
    (root / "t.yaml").write_text(body, encoding="utf-8")
    with pytest.raises(ManifestError, match=r"args\.color\.default"):
        Registry(root).load("t")


def test_flag_arg_default_still_accepts_a_real_bool(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "path"},
                "args": {"q": {"flag": "-q", "type": "flag", "default": True}},
            }
        },
    )
    assert reg.load("t").args["q"].default is True


@pytest.mark.parametrize("repeat", ["no", 0, 1])
def test_arg_repeat_rejects_non_bool(tmp_path: Path, repeat: Any) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "path"},
                "args": {"g": {"flag": "-g", "repeat": repeat}},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"args\.g\.repeat"):
        reg.load("t")


def test_flag_and_positional_together_rejected(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"both": {"flag": "--both", "positional": True}},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"cannot set both flag and positional"):
        reg.load("t")


def test_child_promoting_a_parents_positional_needs_positional_false(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "p.yaml": {"cli": "echo", "abstract": True, "args": {"x": {"positional": True}}},
            "c.yaml": {
                "extends": "p",
                "install": {"backend": "path"},
                "args": {"x": {"flag": "--x"}},
            },
        },
    )
    with pytest.raises(ManifestError, match=r"cannot set both flag and positional"):
        reg.load("c")
    ok = make_reg(
        tmp_path,
        {
            "p2.yaml": {"cli": "echo", "abstract": True, "args": {"x": {"positional": True}}},
            "c2.yaml": {
                "extends": "p2",
                "install": {"backend": "path"},
                "args": {"x": {"flag": "--x", "positional": False}},
            },
        },
    )
    spec = ok.load("c2").args["x"]
    assert spec.flag == "--x"
    assert spec.positional is False


# --- choices: ------------------------------------------------------------------


def test_arg_choices_rejects_a_value_outside_the_set(tmp_path: Path) -> None:
    from maina.core.argparser import ArgParser
    from maina.core.errors import ArgError

    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"mode": {"flag": "--mode", "choices": ["json", "text"]}},
            }
        },
    )
    manifest = reg.load("t")
    parser = ArgParser(manifest.args, tool_id=manifest.id)
    assert parser.build("echo", [], {"mode": "json"}) == ["echo", "--mode", "json"]
    with pytest.raises(ArgError, match=r"must be one of \{json, text\}, got xml"):
        parser.build("echo", [], {"mode": "xml"})


def test_arg_choices_rejects_empty_list_and_flag_type(tmp_path: Path) -> None:
    empty = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"mode": {"flag": "--mode", "choices": []}},
            }
        },
    )
    with pytest.raises(ManifestError, match="choices must be a non-empty list"):
        empty.load("t")

    on_flag = make_reg(
        tmp_path,
        {
            "f.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"v": {"flag": "-v", "type": "flag", "choices": ["a"]}},
            }
        },
    )
    with pytest.raises(ManifestError, match="choices doesn't apply to type: flag"):
        on_flag.load("f")


def test_arg_default_must_be_one_of_its_own_choices(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {
                    "fmt": {"flag": "-f", "default": "json", "choices": ["tty", "gcc"]},
                },
            }
        },
    )
    with pytest.raises(ManifestError, match=r"default 'json' is not one of its own choices"):
        reg.load("t")


def test_pairs_default_must_be_a_mapping_or_pairs_list(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"e": {"flag": "-e", "type": "pairs", "default": "K=V"}},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"args\.e default must be a mapping"):
        reg.load("t")

    ok = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"e": {"flag": "-e", "type": "pairs", "default": {"K": "V"}}},
            }
        },
    )
    assert ok.load("t").args["e"].default == {"K": "V"}


# --- env null-drop and bare-key rejection ---------------------------------------


def test_env_null_drops_an_inherited_var(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "parent.yaml": {
                "cli": "env",
                "abstract": True,
                "env": {"FOO": "parent", "BAR": "keep"},
            },
            "child.yaml": {
                "extends": "parent",
                "install": {"backend": "path"},
                "env": {"FOO": None},
            },
        },
    )
    assert reg.load("child").env == {"BAR": "keep"}


def test_env_bare_key_is_a_load_error_not_the_string_none(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    root.mkdir()
    (root / "t.yaml").write_text("cli: echo\ninstall: {backend: path}\nenv:\n  BLANK:\n", "utf-8")
    with pytest.raises(ManifestError, match=r"env\.BLANK.*not null"):
        Registry(root).load("t")


# --- same-root duplicate id detection -------------------------------------------


def test_duplicate_id_across_files_in_one_root_warns(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "kubectl" / "kubectl.yaml", {"cli": "kubectl", "abstract": True})
    write_yaml(
        root / "kubectl" / "get.yaml",
        {"extends": "kubectl", "subcommand": "get", "install": {"backend": "path"}},
    )
    write_yaml(root / "kubectl-get.yaml", {"cli": "kubectl", "install": {"backend": "path"}})
    registry = Registry(root)
    with pytest.warns(RuntimeWarning, match="'kubectl-get' is derived from 2 manifests"):
        ids = registry.iter_ids()
    assert "kubectl-get" in ids
    dupes = registry.duplicate_ids()
    assert set(dupes) == {"kubectl-get"}
    assert len(dupes["kubectl-get"]) == 2


# --- install.assets / sha256 null-drop across extends --------------------------


def test_child_can_drop_an_inherited_asset_with_null(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "tool.yaml": {
                "abstract": True,
                "cli": "tool",
                "install": {
                    "backend": "github",
                    "repo": "acme/tool",
                    "assets": {"linux-x86_64": "t-1-linux.tgz", "macos-arm64": "t-1-mac.tgz"},
                },
            },
            "v2.yaml": {
                "extends": "tool",
                "version": "2",
                "install": {"assets": {"linux-x86_64": "t-2-linux.tgz", "macos-arm64": None}},
            },
        },
    )
    assets = reg.load("v2").install.assets
    assert assets == {"linux-x86_64": "t-2-linux.tgz"}


# --- arg field combinations argv can only half-honour ---------------------------


def test_join_and_repeat_are_rejected_together(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "args": {"a": {"flag": "--a", "join": ",", "repeat": True}}}},
    )
    with pytest.raises(ManifestError, match="two different list layouts"):
        registry.load("t")


def test_joiner_and_repeat_false_are_rejected_together(tmp_path: Path) -> None:
    """``joiner`` silently beat ``repeat: false``: the author wrote "one flag,
    then every item" and got a joined flag per item."""
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "args": {"e": {"flag": "--e", "repeat": False, "joiner": "="}}}},
    )
    with pytest.raises(ManifestError, match=r"joiner and repeat: false conflict"):
        registry.load("t")


def test_joiner_is_rejected_where_argv_never_emits_it(tmp_path: Path) -> None:
    for name, spec in (
        ("flagged", {"flag": "--b", "type": "flag", "joiner": "="}),
        ("place", {"positional": True, "joiner": "="}),
    ):
        registry = make_reg(tmp_path / name, {"t.yaml": {"cli": "echo", "args": {"c": spec}}})
        with pytest.raises(ManifestError, match="joiner is never emitted"):
            registry.load("t")


def test_a_pairs_arg_may_still_carry_a_joiner(tmp_path: Path) -> None:
    """``docker -e K=V`` is the reason ``joiner`` exists on a pairs arg."""
    registry = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "docker",
                "args": {"e": {"flag": "-e", "type": "pairs", "joiner": "="}},
            }
        },
    )
    assert registry.load("t").args["e"].joiner == "="


def test_clearing_an_inherited_order_does_not_trip_the_separator_conflict(
    tmp_path: Path,
) -> None:
    """``order: null`` is the null-drop idiom resolving the conflict, not
    declaring one."""
    registry = make_reg(
        tmp_path,
        {
            "base.yaml": {"abstract": True, "args": {"p": {"positional": True, "order": 5}}},
            "kid.yaml": {"cli": "echo", "separator": "--", "args": {"p": {"order": None}}},
        },
    )
    manifest = registry.load("kid")
    assert manifest.separator == "--"
    assert manifest.args["p"].order is None


def test_a_real_inherited_order_still_conflicts_with_a_separator(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {
            "base.yaml": {"abstract": True, "args": {"p": {"positional": True, "order": 5}}},
            "kid.yaml": {"cli": "echo", "separator": "--"},
        },
    )
    with pytest.raises(ManifestError, match=r"separator and args\.order"):
        registry.load("kid")


def test_output_allow_stale_report_is_a_declared_boolean(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "output": {"from_file": "r.json", "allow_stale_report": True}}},
    )
    assert registry.load("t").output.allow_stale_report is True
    bad = make_reg(
        tmp_path / "bad",
        {"t.yaml": {"cli": "echo", "output": {"from_file": "r.json", "allow_stale_report": "yes"}}},
    )
    with pytest.raises(ManifestError, match="allow_stale_report must be true or false"):
        bad.load("t")


def test_false_flag_only_applies_to_a_flag_arg(tmp_path: Path) -> None:
    """On any other type it would silently never be emitted."""
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "args": {"a": {"flag": "--a", "false_flag": "--no-a"}}}},
    )
    with pytest.raises(ManifestError, match="false_flag only applies to type: flag"):
        registry.load("t")


def test_a_word_where_a_separator_belongs_is_rejected(tmp_path: Path) -> None:
    """``join:`` is the separator itself, so ``join: csv`` would join the list
    with the literal text "csv" — silently wrong argv."""
    for word, literal in (("csv", "','"), ("comma", "','"), ("semicolon", "';'")):
        registry = make_reg(
            tmp_path / word,
            {"t.yaml": {"cli": "echo", "args": {"a": {"flag": "--a", "join": word}}}},
        )
        with pytest.raises(ManifestError, match="names a separator") as excinfo:
            registry.load("t")
        assert literal in str(excinfo.value)


def test_an_empty_join_separator_is_rejected(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "args": {"a": {"flag": "--a", "join": ""}}}},
    )
    with pytest.raises(ManifestError, match="join must be a non-empty separator"):
        registry.load("t")


# --- fail-closed on non-str/unhashable keys and eager path-safety (review F4/F5/F6/F7) ---


def test_a_bare_yaml_bool_arg_name_is_rejected_not_a_typeerror(tmp_path: Path) -> None:
    """An unquoted ``yes:`` arg name is read as the bool ``True`` by YAML 1.1,
    which used to reach ``ArgSpec`` construction and crash with a raw
    ``TypeError`` instead of a clean ``ManifestError``."""
    registry = make_raw_reg(
        tmp_path,
        {"t.yaml": "cli: echo\nargs:\n  yes:\n    flag: --yes\n    type: flag\n"},
    )
    with pytest.raises(ManifestError, match="args key True must be a quoted string"):
        registry.load("t")


def test_colliding_bare_yaml_bool_env_keys_are_rejected_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """``on:`` and ``yes:`` both resolve to the Python key ``True`` — the second
    one silently overwrote the first with no error. Reject the non-str key
    instead of quietly losing data."""
    registry = make_raw_reg(
        tmp_path,
        {"t.yaml": 'cli: echo\nenv:\n  on: "1"\n  yes: "2"\n'},
    )
    with pytest.raises(ManifestError, match="env key True must be a quoted string"):
        registry.load("t")


def test_a_non_string_platforms_item_is_rejected_not_a_typeerror(tmp_path: Path) -> None:
    """A stray mapping in the ``platforms`` list (e.g. an indentation slip) used
    to crash ``in`` on the ``VALID_PLATFORMS`` frozenset with an unhashable-type
    ``TypeError`` instead of a clean ``ManifestError``."""
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "platforms": ["linux", {"macos": True}]}},
    )
    with pytest.raises(ManifestError, match="platforms"):
        registry.load("t")


def test_cli_path_traversal_is_rejected_by_load_itself_not_only_by_property_access(
    tmp_path: Path,
) -> None:
    """``Registry.load()`` must fail closed on its own — a manifest whose ``cli``
    would escape the cache root must not load successfully and only fail once
    something later reads ``.cli``."""
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "../../escaped/echo"}},
    )
    with pytest.raises(ManifestError, match="not a safe path segment"):
        registry.load("t")
