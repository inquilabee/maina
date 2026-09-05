"""Tests for the ``maina run`` token machinery (RunArgParser / RunInvocation)."""

from __future__ import annotations

import sys

import pytest
import typer

from maina.cli import RunArgParser, RunInvocation


def test_run_arg_parser_kwargs_and_extra() -> None:
    kwargs, extra = RunArgParser.parse(
        ["filter=.", "null_input", "path=a", "path=b", "--", "-c", "--arg", "x", "y"],
        flag_names=frozenset({"null_input"}),
    )
    assert kwargs == {
        "filter": ".",
        "null_input": True,
        "path": ["a", "b"],
    }
    assert extra == ["-c", "--arg", "x", "y"]


def test_run_arg_parser_bare_non_flag_token_rejected() -> None:
    with pytest.raises(typer.BadParameter, match="not a flag"):
        RunArgParser.parse(["verbose"], flag_names=frozenset())


def test_run_arg_parser_bool_coercion() -> None:
    # Coercion only applies to args the manifest declares as type: flag.
    flags = frozenset({"silent", "raw"})
    kwargs, extra = RunArgParser.parse(["silent=true", "raw=false"], flag_names=flags)
    assert kwargs == {"silent": True, "raw": False}
    assert extra is None
    # A non-flag numeric value stays a string.
    plain, _ = RunArgParser.parse(["count=1"])
    assert plain == {"count": "1"}


def test_run_invocation_strips_help_before_extra() -> None:
    tool_id, tokens, wants_help = RunInvocation.parse("shellcheck", ["--help", "--", "--help"])
    assert tool_id == "shellcheck"
    assert wants_help is True
    assert tokens == ["--", "--help"]


def test_run_argparser_raw_string_form() -> None:
    kwargs, _extra = RunArgParser.parse(["count:=1", "flag=1"], flag_names=frozenset({"flag"}))
    assert kwargs["count"] == "1"
    assert kwargs["flag"] is True


def test_run_argparser_colon_eq_after_eq_is_a_value() -> None:
    kwargs, _ = RunArgParser.parse(["filter=.x:=1"])
    assert kwargs == {"filter": ".x:=1"}


def test_run_argparser_pairs_then_plain_collision_is_bad_parameter() -> None:
    with pytest.raises(typer.BadParameter, match=r"already given a plain value"):
        RunArgParser.parse(["env=oops", "env.K=V"])


def test_split_maina_opts_reads_stdin_and_env_allow() -> None:
    opts, rest = RunInvocation.split_maina_opts(
        ["--stdin", "--env-allow=FOO", "--env-allow=BAR,BAZ", "--timeout=5", "path=x"]
    )
    assert opts.stdin is True
    assert opts.env_allow == ("FOO", "BAR", "BAZ")
    assert opts.timeout == pytest.approx(5.0)
    assert rest == ["path=x"]


def test_split_maina_opts_defaults_leave_stdin_and_env_alone() -> None:
    opts, rest = RunInvocation.split_maina_opts(["path=x"])
    assert opts.stdin is False
    assert opts.env_allow is None
    assert rest == ["path=x"]


def test_mixing_pairs_and_plain_tokens_errors_in_either_order() -> None:
    with pytest.raises(typer.BadParameter, match=r"already given a plain value"):
        RunArgParser.parse(["env=plain", "env.K=V"])
    with pytest.raises(typer.BadParameter, match=r"already given env\.key=value pairs"):
        RunArgParser.parse(["env.K=V", "env=plain"])


def test_without_first_separator_mirrors_click() -> None:
    """Click drops exactly the first ``--`` when it builds ``ctx.args``."""
    drop = RunInvocation.without_first_separator
    assert drop(["a", "--", "-n"]) == ["a", "-n"]
    assert drop(["a", "--", "-n", "--", "-e"]) == ["a", "-n", "--", "-e"]
    assert drop(["a", "-n"]) == ["a", "-n"]


def test_separator_recovery_finds_this_invocation_in_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app that mounts maina's commands owns the entry point and never calls
    ``main()``, so the ``--`` has to be recovered from the real argv."""
    monkeypatch.setattr(sys, "argv", ["doctor", "tools", "run", "echoer", "words=a", "--", "-n"])
    recovered = RunInvocation.recover_separator_tokens("echoer", ["words=a", "-n"])
    assert recovered == ["echoer", "words=a", "--", "-n"]


def test_separator_recovery_declines_an_argv_that_is_not_this_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under ``app(args=[...])`` / ``CliRunner``, ``sys.argv`` is the test
    runner's. Nothing may be recovered from it."""
    monkeypatch.setattr(sys, "argv", ["pytest", "-q", "--", "tests/unit"])
    assert RunInvocation.recover_separator_tokens("echoer", ["words=a", "-n"]) is None


def test_separator_recovery_is_skipped_without_a_tool_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "argv", ["maina", "run", "--", "-n"])
    assert RunInvocation.recover_separator_tokens(None, ["-n"]) is None
