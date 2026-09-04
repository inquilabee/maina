"""Tests for Tool.__str__ / Tool.help()."""

from __future__ import annotations

import pytest

from maina.tool import Tool


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
