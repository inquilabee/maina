"""Tests for the ``maina run`` token machinery (RunArgParser / RunInvocation)."""

from __future__ import annotations

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
    with pytest.raises(typer.BadParameter, match="already given a plain value"):
        RunArgParser.parse(["env=oops", "env.K=V"])
