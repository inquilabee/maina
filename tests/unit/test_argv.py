"""Unit tests for ArgParser."""

from __future__ import annotations

import pytest

from maina.core.argparser import ArgParser
from maina.core.errors import ArgError


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


@pytest.mark.parametrize("value", [{"a": 1}, {"a"}, object()])
def test_build_argv_rejects_non_scalar_value(value: object) -> None:
    schema = {"x": {"flag": "--x", "type": "string"}}
    with pytest.raises(ArgError, match="must be a string or number"):
        ArgParser(schema).build("tool", [], {"x": value})


@pytest.mark.parametrize("value", [True, False])
def test_build_argv_rejects_bool_for_a_non_flag_arg(*, value: bool) -> None:
    schema = {"name": {"flag": "--name", "type": "string"}}
    with pytest.raises(ArgError, match="must be a string or number, not bool"):
        ArgParser(schema).build("echo", [], {"name": value})


def test_build_argv_rejects_bool_inside_a_list_value() -> None:
    schema = {"flags": {"flag": "--flags", "repeat": True}}
    with pytest.raises(ArgError, match="must be a string or number, not bool"):
        ArgParser(schema).build("echo", [], {"flags": [True, "x"]})


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


def test_build_argv_pairs_default_three_tokens() -> None:
    schema = {"arg": {"flag": "--arg", "type": "pairs"}}
    argv = ArgParser(schema).build("jq", [], {"arg": {"name": "v"}})
    assert argv == ["jq", "--arg", "name", "v"]


def test_build_argv_pairs_with_joiner_two_tokens() -> None:
    schema = {"env": {"flag": "-e", "type": "pairs", "joiner": "="}}
    argv = ArgParser(schema).build("docker", ["run"], {"env": {"K": "V", "A": "B"}})
    assert argv == ["docker", "run", "-e", "K=V", "-e", "A=B"]


def test_build_argv_separator_before_positionals() -> None:
    schema = {
        "n": {"flag": "--max-count", "joiner": "=", "type": "string"},
        "paths": {"positional": True, "repeat": True, "type": "path"},
    }
    argv = ArgParser(schema).build("git", ["log"], {"n": "3", "paths": ["-x"]}, separator="--")
    assert argv == ["git", "log", "--max-count=3", "--", "-x"]


def test_build_argv_separator_omitted_when_no_positionals() -> None:
    schema = {"n": {"flag": "-n", "type": "string"}}
    argv = ArgParser(schema).build("t", [], {"n": "3"}, separator="--")
    assert argv == ["t", "-n", "3"]


def test_build_argv_extra_lands_before_the_separator() -> None:
    schema = {"paths": {"positional": True, "repeat": True, "type": "path"}}
    argv = ArgParser(schema).build(
        "git", ["log"], {"paths": ["a"]}, extra=["--stat"], separator="--"
    )
    assert argv == ["git", "log", "--stat", "--", "a"]


def test_build_argv_extra_stays_last_without_a_separator() -> None:
    schema = {"paths": {"positional": True, "repeat": True, "type": "path"}}
    argv = ArgParser(schema).build("git", ["log"], {"paths": ["a"]}, extra=["--stat"])
    assert argv == ["git", "log", "a", "--stat"]


def test_build_argv_errors_name_the_tool_and_known_args() -> None:
    parser = ArgParser({"filter": {"positional": True}}, tool_id="jq")
    with pytest.raises(ArgError, match=r"jq: unknown arguments: nope \(known: filter\)"):
        parser.build("jq", [], {"nope": 1})
    strict = ArgParser({"name": {"positional": True, "required": True}}, tool_id="jq")
    with pytest.raises(ArgError, match=r"jq: missing required argument: name \(known: name\)"):
        strict.build("jq", [], {})


def test_build_argv_order_puts_option_after_positional() -> None:
    schema = {
        "name": {"flag": "-name", "type": "string", "order": 2},
        "path": {"positional": True, "type": "path", "order": 1},
    }
    argv = ArgParser(schema).build("find", [], {"path": ".", "name": "*.py"})
    assert argv == ["find", ".", "-name", "*.py"]


def test_build_argv_default_order_is_options_then_positionals() -> None:
    schema = {
        "path": {"positional": True, "type": "path"},
        "flag": {"flag": "--flag", "type": "flag"},
    }
    argv = ArgParser(schema).build("t", [], {"path": "x", "flag": True})
    assert argv == ["t", "--flag", "x"]


def test_build_argv_pairs_rejects_string_item() -> None:
    schema = {"arg": {"flag": "--arg", "type": "pairs"}}
    with pytest.raises(ArgError, match="dict or a list of"):
        ArgParser(schema).build("t", [], {"arg": ["ab", "cd"]})


def test_pairs_values_go_through_the_same_scalar_guard_as_every_other_token() -> None:
    """``env={"DEBUG": False}`` must not emit ``-e DEBUG=False``.

    Most CLIs read a set-but-"False" variable as set and truthy, so stringifying
    a bool here is the silent-wrong-command the guard exists to prevent.
    """
    parser = ArgParser({"env": {"flag": "-e", "type": "pairs", "joiner": "="}})
    for bad in (False, True, None, ["a", "b"], {"y": 1}):
        with pytest.raises(ArgError, match="pairs value must be a string or number"):
            parser.build("docker", [], {"env": {"DEBUG": bad}})
    assert parser.build("docker", [], {"env": {"K": "V", "N": 3}}) == [
        "docker",
        "-e",
        "K=V",
        "-e",
        "N=3",
    ]


def test_pairs_keys_go_through_the_scalar_guard_too() -> None:
    parser = ArgParser({"arg": {"flag": "--arg", "type": "pairs"}})
    with pytest.raises(ArgError, match="pairs key must be a string or number"):
        parser.build("jq", [], {"arg": [(True, "v")]})
