"""Regression tests: manifest load, inheritance merge, and load-time validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from helpers import fixture_runtime, write_yaml
from maina.backends import PathBackend
from maina.core.argparser import ArgParser
from maina.core.errors import InstallError, ManifestError
from maina.core.parsers import ParserResolver, builtin_generals
from maina.core.registry import Registry
from maina.installer import Installer
from maina.runner import ToolRunner
from maina.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


def path_installer(tmp_path: Path, reg: Registry) -> Installer:
    return Installer(cache_root=tmp_path / "cache", registry=reg, backends=[PathBackend()])


# --- P0: manifest field path traversal ---------------------------------------


def test_manifest_cli_rejects_path_escape(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path, {"evil.yaml": {"cli": "../../escaped/echo", "install": {"backend": "path"}}}
    )
    with pytest.raises(ManifestError, match="not a safe path segment"):
        _ = reg.load("evil").cli


def test_manifest_version_rejects_separator(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "version": "../x", "install": {"backend": "path"}}},
    )
    with pytest.raises(ManifestError, match="not a safe path segment"):
        _ = reg.load("t").version


def test_installer_resolve_refuses_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = make_reg(tmp_path, {"t.yaml": {"cli": "echo", "install": {"backend": "path"}}})
    manifest = reg.load("t")
    monkeypatch.setattr(type(manifest), "cli", property(lambda _self: "../../pwned"))
    with pytest.raises(InstallError, match="escapes the cache root"):
        path_installer(tmp_path, reg).resolve(manifest)


# --- P1: abstract not inherited --------------------------------------------


def test_abstract_not_inherited(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "git/git.yaml": {"cli": "git", "abstract": True, "install": {"backend": "path"}},
            "git/log.yaml": {"extends": "git", "subcommand": "log"},
        },
    )
    assert reg.load("git").abstract is True
    assert reg.load("git-log").abstract is False
    assert "git-log" in reg.list_commands(None)


# --- P1: hyphenated nested filename ---------------------------------------------


def test_hyphenated_nested_id_resolves(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path, {"family/my-tool.yaml": {"cli": "echo", "install": {"backend": "path"}}}
    )
    assert reg.iter_ids() == ["family-my-tool"]
    assert reg.load("family-my-tool").cli == "echo"
    assert reg.list_commands(None) == ["family-my-tool"]


def test_yml_suffix_supported(tmp_path: Path) -> None:
    reg = make_reg(tmp_path, {"tool.yml": {"cli": "echo", "install": {"backend": "path"}}})
    assert "tool" in reg.iter_ids()
    assert reg.load("tool").cli == "echo"


# --- P1: non-UTF-8 output / stdin -------------------------------------------


def test_non_utf8_output_does_not_crash(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "raw.yaml": {
                "cli": "printf",
                "install": {"backend": "path"},
                "args": {"fmt": {"positional": True, "type": "string", "required": True}},
                "output": {"success_codes": [0]},
            }
        },
    )
    result = ToolRunner(reg.load("raw"), installer=path_installer(tmp_path, reg)).run(
        {"fmt": "\\xff\\xfe bad"}
    )
    assert result.ok
    assert "bad" in result.raw


def test_stdin_input_is_forwarded(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "cat.yaml": {
                "cli": "cat",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    result = ToolRunner(reg.load("cat"), installer=path_installer(tmp_path, reg)).run(
        {}, stdin="piped-in\n"
    )
    assert result.raw == "piped-in\n"


# --- P1: --flag=value via joiner --------------------------------------------


def test_joiner_emits_equals_form() -> None:
    schema = {"format": {"flag": "--format", "joiner": "=", "type": "string"}}
    argv = ArgParser(schema).build("git", ["log"], {"format": "%h %s"})
    assert argv == ["git", "log", "--format=%h %s"]


def test_joiner_on_repeated_flag() -> None:
    schema = {"opt": {"flag": "-o", "joiner": "=", "type": "string", "repeat": True}}
    argv = ArgParser(schema).build("tool", [], {"opt": ["a", "b"]})
    assert argv == ["tool", "-o=a", "-o=b"]


# --- validation at load ------------------------------------------------------


def test_unknown_top_level_key_raises(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path, {"t.yaml": {"cli": "echo", "outputs": {}, "install": {"backend": "path"}}}
    )
    with pytest.raises(ManifestError, match="unknown top-level keys: outputs"):
        reg.load("t")


def test_unknown_arg_type_raises(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"x": {"type": "bool", "flag": "-x"}},
            }
        },
    )
    with pytest.raises(ManifestError, match="type 'bool' not in"):
        reg.load("t")


def test_flagless_non_positional_raises(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"x": {"type": "string"}},
            }
        },
    )
    with pytest.raises(ManifestError, match="needs a flag or positional"):
        reg.load("t")


def test_unknown_parser_name_surfaces_on_parse_error_not_crash(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "parser": "jsonn",
                "args": {"m": {"positional": True, "type": "string", "required": True}},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(
        reg.load("t"),
        installer=path_installer(tmp_path, reg),
        parsers=ParserResolver(generals=builtin_generals()),
    )
    result = runner.run({"m": "hi"})
    assert result.parsed is None
    assert result.parse_error is not None
    assert "unknown parser 'jsonn'" in result.parse_error


def test_bad_schema_version_raises(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path, {"t.yaml": {"schema": 99, "cli": "echo", "install": {"backend": "path"}}}
    )
    with pytest.raises(ManifestError, match="schema 99 unsupported"):
        reg.load("t")


def test_args_inherit_merges_per_field(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "base.yaml": {
                "abstract": True,
                "args": {"fmt": {"flag": "--fmt", "type": "string", "default": "json"}},
            },
            "child.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {"fmt": {"default": "yaml"}},
            },
        },
    )
    spec = reg.load("child").args["fmt"]
    assert spec.flag == "--fmt"
    assert spec.default == "yaml"


# --- Runtime.preferred kwargs / hermetic -----------------------------------


def test_preferred_accepts_cache_root_and_hermetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAINA_REGISTRY", str(tmp_path / "ignored"))
    monkeypatch.setenv("MAINA_CACHE", str(tmp_path / "ignored-cache"))
    rt = Runtime.preferred(cache_root=tmp_path / "explicit", use_env=False)
    assert rt.cache_root == tmp_path / "explicit"
    assert rt.registry.iter_ids() == []
    rt.close()


def test_preferred_registry_dirs_kwarg(tmp_path: Path) -> None:
    make_reg(tmp_path, {"solo.yaml": {"cli": "echo", "install": {"backend": "path"}}})
    rt = Runtime.preferred(registry_dirs=[tmp_path / "reg"], use_env=False)
    assert rt.registry.load("solo").cli == "echo"


def test_runtime_context_manager_closes(tmp_path: Path) -> None:
    with Runtime.preferred(cache_root=tmp_path / "c", use_env=False) as rt:
        assert rt.cache_root == tmp_path / "c"


# --- CLI: type-aware coercion / --json / empty hint -----------------------


def test_cli_list_empty_teaches(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    import maina
    from maina.cli import app

    monkeypatch.delenv("MAINA_REGISTRY", raising=False)
    maina.use(Runtime.preferred(use_env=False))
    try:
        out = CliRunner().invoke(app, ["list"])
        assert "MAINA_REGISTRY" in out.stdout
    finally:
        maina.use(fixture_runtime())


def test_cli_usage_error_exit_2() -> None:
    from maina.cli import main

    assert main(["list", "a", "b", "c"]) == 2


def test_cli_emit_result_prints_raw_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    from maina.cli import emit_result
    from maina.core.result import ToolResult

    result = ToolResult(
        tool="t", ok=True, exit_code=0, raw='{"a": 1}\n', stderr="", command=["echo"]
    )
    result.parsed = {"a": 1}
    emit_result(result)
    assert capsys.readouterr().out == '{"a": 1}\n'
    emit_result(result, as_json=True)
    assert '"a": 1' in capsys.readouterr().out


def test_cli_run_coercion_is_type_aware(tmp_path: Path) -> None:
    from maina.cli import RunArgParser

    reg = make_reg(
        tmp_path,
        {
            "demo.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "args": {
                    "count": {"flag": "--count", "type": "string"},
                    "silent": {"flag": "--silent", "type": "flag"},
                },
            }
        },
    )
    schema = reg.load("demo").args
    flags = frozenset(name for name, spec in schema.items() if spec.type == "flag")
    kwargs, _ = RunArgParser.parse(["count=1", "silent=1"], flag_names=flags)
    assert kwargs == {"count": "1", "silent": True}
