"""Unit tests for ParserResolver / general parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyporter.errors import ManifestError
from pyporter.parsers import ParserResolver
from pyporter.result import ToolResult
from pyporter.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


def make_result(raw: str = "") -> ToolResult:
    return ToolResult(
        tool="test",
        ok=True,
        exit_code=0,
        raw=raw,
        stderr="",
        command=["test"],
    )


def builtins_resolver() -> ParserResolver:
    resolver = ParserResolver()
    resolver.register_many(
        {
            "json": ParserResolver.parse_json,
            "lines": ParserResolver.parse_lines,
            "text": ParserResolver.parse_text,
            "raw": ParserResolver.parse_text,
        }
    )
    return resolver


def test_generic_json_semantics() -> None:
    parse = builtins_resolver().generic_parser("json")
    assert parse("", make_result()) is None
    assert parse("   ", make_result()) is None
    assert parse('{"a": 1}', make_result()) == {"a": 1}
    assert parse("not-json", make_result()) == "not-json"


def test_parser_ref_json_resolves_without_path() -> None:
    resolver = builtins_resolver().bind(parser_ref="json", parser_path=None)
    resolved = resolver.resolve()
    assert resolved is not None
    out = resolver.apply(make_result('{"ok": true}'))
    assert out.parsed == {"ok": True}


def test_apply_empty_raw_is_none() -> None:
    resolver = builtins_resolver().bind(parser_ref="json")
    out = resolver.apply(make_result(""))
    assert out.parsed is None


def test_register_parser_callable() -> None:
    resolver = ParserResolver()

    def parse_csv(raw: str, _result: ToolResult) -> list[str]:
        return raw.split(",")

    resolver.register("csv", parse_csv)
    assert resolver.generic_parser("csv")("a,b", make_result()) == ["a", "b"]


def test_register_parsers_dir(tmp_path: Path) -> None:
    parsers_dir = tmp_path / "parsers"
    parsers_dir.mkdir()
    (parsers_dir / "foo.py").write_text(
        "def parse(raw, result):\n    return f'foo:{raw}'\n",
        encoding="utf-8",
    )
    resolver = ParserResolver()
    resolver.load_parsers_dirs([parsers_dir])
    assert resolver.generic_parser("foo")("x", make_result()) == "foo:x"


def test_parser_name_replace() -> None:
    resolver = ParserResolver()
    resolver.register("mode", lambda _raw, _r: "first")
    resolver.register("mode", lambda _raw, _r: "second")
    assert resolver.generic_parser("mode")("x", make_result()) == "second"


def test_invalid_parser_name_errors() -> None:
    resolver = ParserResolver()
    with pytest.raises(ManifestError, match="invalid general parser name"):
        resolver.register("bad.py", lambda raw, _r: raw)
    with pytest.raises(ManifestError, match="invalid general parser name"):
        resolver.register("a/b", lambda raw, _r: raw)


def test_no_classvar_leakage_across_instances() -> None:
    a = ParserResolver()
    b = ParserResolver()
    a.register("only_a", lambda raw, _r: raw)
    assert a.is_general("only_a")
    assert not b.is_general("only_a")


def test_runtime_registers_builtin_parsers() -> None:
    rt = Runtime.preferred()
    assert rt.parsers.is_general("json")
    out = rt.parsers.bind(parser_ref="json").apply(make_result('{"n": 1}'))
    assert out.parsed == {"n": 1}
