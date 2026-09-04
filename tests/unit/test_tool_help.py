"""Tests for Tool.__str__ / Tool.help() / Tool.argv()."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.backends import PathBackend
from maina.runtime import Runtime, register
from maina.tool import Tool

if TYPE_CHECKING:
    from pathlib import Path


def test_str_bound_tool_owns_help_block() -> None:
    text = str(Tool("jq"))
    assert text.startswith("Tool: jq")
    assert "cli: jq" in text
    assert "abstract:" not in text
    assert "Arguments:" in text
    assert "filter:" in text


def test_str_abstract_tool_shows_abstract_true() -> None:
    text = str(Tool("kubectl"))
    assert text.startswith("Tool: kubectl")
    assert "abstract: true" in text
    assert "abstract: False" not in text
    assert "abstract: None" not in text


def test_str_shellcheck_includes_arg_descriptions() -> None:
    text = str(Tool("shellcheck"))
    assert "Tool: shellcheck" in text
    assert "cli: shellcheck" in text
    assert "path:" in text
    assert "Script file" in text


def test_str_unbound_is_library_help() -> None:
    text = str(Tool())
    assert "Registered tools:" in text
    assert "import maina" in text
    assert "maina run" not in text
    assert "shellcheck" in text
    bullets = [
        line.strip().removeprefix("- ").strip()
        for line in text.split("Registered tools:", 1)[1].splitlines()
        if line.strip().startswith("-")
    ]
    assert "kubectl-get" in bullets
    assert "kubectl" not in bullets
    assert "base" not in bullets
    assert "git" not in bullets


def test_help_returns_str(capsys: pytest.CaptureFixture[str]) -> None:
    tool = Tool("jq")
    returned = tool.help()
    captured = capsys.readouterr()
    assert returned == str(tool)
    assert captured.out == ""


def test_tool_argv_previews_argv_without_running() -> None:
    argv = Tool("jq").argv(filter=".", compact=True)
    assert argv == ["jq", "-c", "."]


def test_argv_agrees_with_run_for_a_reserved_param_name(tmp_path: Path) -> None:
    """A manifest arg named like a Tool.run parameter (here `timeout`) binds to
    that parameter in both argv() and run() — neither silently treats it as
    the manifest arg unless routed through args={...}, so a --dry-run preview
    never disagrees with what run() actually sends to argv."""
    root = tmp_path / "reg"
    write_yaml(
        root / "collide.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "args": {"timeout": {"flag": "--timeout"}}},
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    register(rt, registry_dirs=[root], installers=[PathBackend()])
    tool = Tool("collide", runtime=rt)
    assert tool.argv(timeout=5) == ["echo"]
    assert tool.argv(args={"timeout": "5"}) == ["echo", "--timeout", "5"]
    result = tool.run(timeout=5, args={"timeout": "9"})
    assert result.command[1:] == ["--timeout", "9"]


def test_run_rejects_a_non_mapping_env_with_arg_error(tmp_path: Path) -> None:
    from typing import Any

    from maina.core.errors import ArgError

    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "echo", "install": {"backend": "path"}})
    rt = Runtime(cache_root=tmp_path / "cache")
    register(rt, registry_dirs=[root])
    bad_env: Any = "production"  # a manifest bug (or a caller typo), not a real mapping
    with pytest.raises(ArgError, match="env= must be a mapping"):
        Tool("t", runtime=rt).run(env=bad_env)
