"""Unit tests for ParserResolver / general parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from maina.core.errors import ManifestError, ParseError
from maina.core.parsers import ParserResolver, builtin_generals
from maina.core.result import ToolResult
from maina.runtime import Runtime

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
    return ParserResolver(generals=builtin_generals())


def test_generic_json_semantics() -> None:
    parse = builtins_resolver().generic_parser("json")
    assert parse("", make_result()) is None
    assert parse("   ", make_result()) is None
    assert parse('{"a": 1}', make_result()) == {"a": 1}
    with pytest.raises(ParseError, match="not valid JSON"):
        parse("not-json", make_result())
    lenient = builtins_resolver().generic_parser("json-lenient")
    assert lenient("not-json", make_result()) == "not-json"


def test_apply_records_parse_error_not_raise() -> None:
    out = builtins_resolver().bind(parser_ref="json").apply(make_result("not-json"))
    assert out.parsed is None
    assert out.parse_error is not None
    assert "not valid JSON" in out.parse_error


def test_broken_parser_module_records_parse_error_not_raise(tmp_path: Path) -> None:
    mod = tmp_path / "boom.py"
    mod.write_text("raise RuntimeError('import blew up')\n", encoding="utf-8")
    out = builtins_resolver().bind(parser_path=mod).apply(make_result("x"))
    assert out.parsed is None
    assert out.parse_error is not None
    assert "failed to import" in out.parse_error


def test_apply_runs_parser_on_empty_stdout() -> None:
    seen: list[str] = []

    def note(raw: str, _r: ToolResult) -> str:
        seen.append(raw)
        return "done"

    out = builtins_resolver().bind(parser_ref=None, override=note).apply(make_result(""))
    assert seen == [""]
    assert out.parsed == "done"


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


def test_builtins_satisfy_parser_protocol() -> None:
    from maina.core.parsers import FunctionParser, parse_json, parse_lines, parse_text, parser
    from maina.core.protocols import Parser

    assert isinstance(parse_json, Parser)
    assert isinstance(parse_lines, Parser)
    assert isinstance(parse_text, Parser)
    wrapped = parser(parse_json)
    assert isinstance(wrapped, FunctionParser)
    assert isinstance(wrapped, Parser)
    assert parser(wrapped) is wrapped


def test_register_wraps_via_parser_factory() -> None:
    from maina.core.parsers import FunctionParser

    resolver = ParserResolver()

    def parse_csv(raw: str, _result: ToolResult) -> list[str]:
        return raw.split(",")

    resolver.register("csv", parse_csv)
    registered = resolver.generic_parser("csv")
    assert isinstance(registered, FunctionParser)
    assert registered("a,b", make_result()) == ["a", "b"]


def test_register_rejects_non_parser() -> None:
    from typing import Any

    resolver = ParserResolver()
    bad: Any = "not-callable"
    with pytest.raises(ManifestError, match="must be a Parser callable"):
        resolver.register("bad", bad)


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
