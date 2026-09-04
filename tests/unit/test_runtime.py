"""Tests for composition Runtime, register, and use."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import maina
from helpers import fixture_runtime, write_yaml
from maina.core.errors import ManifestError, RegisterError
from maina.core.parsers import ParserResolver
from maina.runtime import Runtime, resolve_cache_root

if TYPE_CHECKING:
    from pathlib import Path


def test_bootstrap_has_parsers_and_no_catalog() -> None:
    preferred = Runtime.preferred()
    # maina ships no tool catalog.
    assert preferred.registry.iter_ids() == []
    assert preferred.parsers.is_general("json")
    assert preferred.parsers.is_general("raw")
    # A caller-supplied catalog is what makes tools loadable.
    assert fixture_runtime().registry.load("jq").cli == "jq"


def test_runtime_use_isolates() -> None:
    maina.use(Runtime())
    with pytest.raises(ManifestError):
        maina.Tool("jq")
    maina.use(fixture_runtime())
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
    # Active session has the fixture catalog; explicit runtime only sees local.
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


def test_discover_warns_on_a_broken_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata as md

    class _Broken:
        name = "busted"

        def load(self) -> object:
            raise RuntimeError("catalog import blew up")

    class _NotADir:
        name = "ghost"

        def load(self) -> str:
            return "/no/such/registry/dir"

    monkeypatch.setattr(md, "entry_points", lambda **_kw: [_Broken(), _NotADir()])
    with pytest.warns(RuntimeWarning) as caught:
        dirs = Runtime._discovered_registry_dirs()
    assert dirs == []
    messages = " ".join(str(w.message) for w in caught)
    assert "busted" in messages
    assert "ghost" in messages


def test_resolve_cache_root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAINA_CACHE", str(tmp_path / "env-cache"))
    root = resolve_cache_root(None)
    assert root == tmp_path / "env-cache"
    assert root.is_dir()
    explicit = resolve_cache_root(tmp_path / "explicit")
    assert explicit == tmp_path / "explicit"
