"""Unit tests for ArgParser."""

from __future__ import annotations

import pytest

from pyporter.core.argparser import ArgParser
from pyporter.core.errors import ArgError


def test_build_argv_flags_and_positionals() -> None:
    schema = {
        "path": {"positional": True, "repeat": True, "type": "path"},
        "min_lines": {"flag": "--min-lines", "type": "string"},
        "silent": {"flag": "--silent", "type": "flag"},
    }
    argv = ArgParser(schema).build(
        "jscpd",
        [],
        {"path": ["src", "tests"], "min_lines": "5", "silent": True},
    )
    assert argv == ["jscpd", "--min-lines", "5", "--silent", "src", "tests"]


def test_build_argv_subcommands_and_extra() -> None:
    schema = {
        "name": {"positional": True, "type": "string", "required": True},
        "url": {"positional": True, "type": "string", "required": True},
    }
    argv = ArgParser(schema).build(
        "git",
        ["remote", "add"],
        {"name": "origin", "url": "https://example.com"},
        extra="--verbose",
    )
    assert argv == ["git", "remote", "add", "origin", "https://example.com", "--verbose"]


def test_build_argv_repeated_flags() -> None:
    schema = {"glob": {"flag": "--glob", "type": "string", "repeat": True}}
    argv = ArgParser(schema).build("rg", [], {"glob": ["*.py", "*.md"]})
    assert argv == ["rg", "--glob", "*.py", "--glob", "*.md"]


def test_build_argv_csv_join() -> None:
    schema = {"tags": {"flag": "--tags", "type": "string", "join": "csv"}}
    argv = ArgParser(schema).build("tool", [], {"tags": ["a", "b"]})
    assert argv == ["tool", "--tags", "a,b"]


def test_build_argv_unknown_kwarg() -> None:
    with pytest.raises(ArgError, match="unknown"):
        ArgParser({"filter": {"positional": True}}).build("jq", [], {"nope": 1})


def test_build_argv_extra_list() -> None:
    argv = ArgParser({}).build("jq", [], {}, extra=["-c", "-r"])
    assert argv == ["jq", "-c", "-r"]


def test_build_argv_missing_required() -> None:
    schema = {"name": {"positional": True, "type": "string", "required": True}}
    with pytest.raises(ArgError, match="missing required argument: name"):
        ArgParser(schema).build("git", ["remote", "add"], {})


def test_build_argv_flag_rejects_non_bool() -> None:
    schema = {"silent": {"flag": "--silent", "type": "flag"}}
    with pytest.raises(ArgError, match="expects bool"):
        ArgParser(schema).build("tool", [], {"silent": "yes"})


def test_build_argv_repeat_false_keeps_single_flag() -> None:
    schema = {"glob": {"flag": "--glob", "type": "string", "repeat": False}}
    argv = ArgParser(schema).build("rg", [], {"glob": ["*.py", "*.md"]})
    assert argv == ["rg", "--glob", "*.py", "*.md"]


def test_build_argv_applies_default() -> None:
    schema = {
        "format": {"flag": "-f", "type": "string", "default": "gcc"},
        "path": {"positional": True, "type": "path", "required": True},
    }
    argv = ArgParser(schema).build("shellcheck", [], {"path": "x.sh"})
    assert argv == ["shellcheck", "-f", "gcc", "x.sh"]


def test_build_argv_empty_list_omits_flag() -> None:
    schema = {"glob": {"flag": "--glob", "type": "string", "repeat": True}}
    argv = ArgParser(schema).build("rg", [], {"glob": []})
    assert argv == ["rg"]
