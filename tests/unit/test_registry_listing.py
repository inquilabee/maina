"""Tests for Registry.list_commands and hyphen-id lookup / scan fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from helpers import fixture_registry, write_yaml
from maina.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path


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


def test_scan_fallback_rescans_a_root_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hyphenated filename inside a subdirectory only resolves via the
    scan-fallback (`family/my-tool.yaml` -> id `family-my-tool` misses every
    fast candidate); loading several such ids from one root must not rglob
    the tree again for each of them."""
    from pathlib import Path as PathType

    root = tmp_path / "registry"
    for name in ("a", "b", "c"):
        write_yaml(
            root / "family" / f"my-{name}.yaml", {"cli": name, "install": {"backend": "path"}}
        )
    registry = Registry(root)

    calls = 0
    real_rglob = PathType.rglob

    def counting_rglob(self: Path, pattern: str = "*", **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real_rglob(self, pattern, **kwargs)

    monkeypatch.setattr(PathType, "rglob", counting_rglob)
    for name in ("a", "b", "c"):
        assert registry.load(f"family-my-{name}").cli == name
    assert calls == 1


def test_list_commands_finds_a_descendant_with_no_manifest_at_the_intermediate_level(
    tmp_path: Path,
) -> None:
    """``family/foo/bar.yaml`` extending the family root directly (skipping a
    ``foo`` manifest that does not exist) must still show up under
    ``list_commands('family')`` — the grouping directory is not itself a
    surface, so nothing should try to load an id for it."""
    root = tmp_path / "registry"
    write_yaml(root / "git" / "git.yaml", {"cli": "git", "abstract": True})
    write_yaml(
        root / "git" / "foo" / "bar.yaml",
        {"extends": "git", "subcommand": "bar", "install": {"backend": "path"}},
    )
    registry = Registry(root)
    assert registry.list_commands("git") == ["git-foo-bar"]


def test_list_commands_skips_broken_manifest_with_warning(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "good", "install": {"backend": "path"}})
    (root / "broken.yaml").write_text("cli: broken\noutputs: {}\n", encoding="utf-8")
    with pytest.warns(RuntimeWarning, match="broken"):
        ids = Registry(root).list_commands()
    assert ids == ["good"]


def test_refresh_sees_manifests_added_to_an_already_scanned_root(tmp_path: Path) -> None:
    """A root is scanned once and the index reused; ``refresh()`` drops it.

    Without this a long-lived process (a service, a notebook, a watch loop)
    shows a permanently stale catalog, and an id only the tree scan can resolve
    is not merely unlisted but unloadable.
    """
    root = tmp_path / "reg"
    write_yaml(root / "first.yaml", {"cli": "echo"})
    registry = Registry([root])
    assert registry.list_commands(None) == ["first"]

    write_yaml(root / "second.yaml", {"cli": "echo"})
    write_yaml(root / "fam" / "my-tool.yaml", {"cli": "echo"})
    assert registry.list_commands(None) == ["first"]

    registry.refresh()
    assert registry.list_commands(None) == ["fam-my-tool", "first", "second"]
    # `fam-my-tool` splits on every hyphen, so only the tree scan resolves it.
    assert registry.load("fam-my-tool").cli == "echo"


def test_a_manifest_edited_in_place_is_reparsed(tmp_path: Path) -> None:
    """The raw-YAML memo is keyed on the file's own mtime + size, so an edit is
    picked up without a ``refresh()`` — only the *set of files* is cached."""
    root = tmp_path / "reg"
    path = write_yaml(root / "t.yaml", {"cli": "echo"})
    registry = Registry([root])
    assert registry.load("t").cli == "echo"
    write_yaml(path, {"cli": "printf"})
    assert registry.load("t").cli == "printf"
