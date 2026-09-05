"""Tests for composition Runtime, register, and use."""

from __future__ import annotations

import atexit
from typing import TYPE_CHECKING

import pytest

import maina
from helpers import fixture_runtime, write_yaml
from maina.backends import PathBackend
from maina.core.errors import ManifestError, RegisterError
from maina.core.parsers import ParserResolver
from maina.runtime import Runtime, resolve_cache_root

if TYPE_CHECKING:
    from pathlib import Path


def test_bootstrap_has_parsers_and_no_catalog() -> None:
    preferred = Runtime.preferred(use_env=False)
    # maina ships no tool catalog.
    assert preferred.registry.iter_ids() == []
    assert preferred.parsers.is_general("json")
    assert preferred.parsers.is_general("raw")
    # A caller-supplied catalog is what makes tools loadable.
    assert fixture_runtime().registry.load("jq").cli == "jq"


def test_use_registers_atexit_close_for_an_explicit_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Runtime installed explicitly via use(rt) must get its close()
    registered at exit too — get()'s lazily-built default already did this;
    set() (what use() calls) did not."""
    registered: list[object] = []
    # Patch the real stdlib atexit module directly (imported here too): it's
    # the same singleton object maina.runtime's own `import atexit` refers
    # to, so this doesn't need to reach maina.runtime's own namespace at
    # all — handy since maina/__init__.py's `from maina.runtime import ...
    # runtime, use` rebinds the `maina.runtime` package attribute to that
    # function, shadowing the submodule for any dotted-path lookup.
    monkeypatch.setattr(atexit, "register", registered.append)
    rt = Runtime()
    maina.use(rt)
    assert rt.close in registered


def test_use_unregisters_the_previous_atexit_hook_on_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Swapping the active Runtime repeatedly must not accumulate one atexit
    hook per swap, keeping every superseded Runtime alive until process
    exit — each swap replaces the previous hook."""
    registered: list[object] = []
    unregistered: list[object] = []
    monkeypatch.setattr(atexit, "register", registered.append)
    monkeypatch.setattr(atexit, "unregister", unregistered.append)
    rt1 = Runtime()
    rt2 = Runtime()
    maina.use(rt1)
    maina.use(rt2)
    # The autouse restore_session_runtime fixture already swapped the active
    # Runtime once before this test body runs, so assert the tail of each
    # list (this swap's effect) rather than the whole history.
    assert registered[-2:] == [rt1.close, rt2.close]
    assert unregistered[-1] == rt1.close


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


def test_register_returns_a_bound_app(tmp_path: Path) -> None:
    root = tmp_path / "tools"
    write_yaml(root / "local.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = Runtime()
    app = maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    assert isinstance(app, maina.MainaApp)
    assert app.runtime is rt
    assert app.tool("local").id == "local"
    assert app.tool("local").run().ok


def test_two_apps_do_not_interfere(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    write_yaml(root_a / "only-a.yaml", {"cli": "echo", "install": {"backend": "path"}})
    write_yaml(root_b / "only-b.yaml", {"cli": "echo", "install": {"backend": "path"}})
    app_a = maina.register(Runtime(), registry_dirs=[root_a], installers=[PathBackend()])
    app_b = maina.register(Runtime(), registry_dirs=[root_b], installers=[PathBackend()])
    assert app_a.tool("only-a").id == "only-a"
    assert app_b.tool("only-b").id == "only-b"
    with pytest.raises(ManifestError):
        app_a.tool("only-b")
    with pytest.raises(ManifestError):
        app_b.tool("only-a")


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
