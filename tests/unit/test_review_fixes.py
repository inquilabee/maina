"""Regression tests for the second blind-review findings (P0 + P1s + validation)."""

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


def test_unknown_parser_name_raises_at_run(tmp_path: Path) -> None:
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
    with pytest.raises(ManifestError, match="unknown parser 'jsonn'"):
        runner.run({"m": "hi"})


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


# --- review C: install-path security (B1, B2, B3) -------------------------------


def test_install_asset_name_traversal_rejected(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {
                    "backend": "github",
                    "repo": "acme/t",
                    "assets": {"linux-x86_64": "../../../etc/evil"},
                },
            }
        },
    )
    with pytest.raises(ManifestError, match="not a safe path segment"):
        reg.load("t")


def test_install_repo_shape_validated(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "t", "install": {"backend": "github", "repo": "not-a-repo"}}},
    )
    with pytest.raises(ManifestError, match="owner/name"):
        reg.load("t")


def test_identity_key_distinguishes_specs() -> None:
    from maina.core.registry import InstallSpec

    a = InstallSpec(backend="github", repo="acme/x", tag="v1", assets={"k": "x-1.tgz"})
    b = InstallSpec(backend="github", repo="other/x", tag="v1", assets={"k": "x-1.tgz"})
    c = InstallSpec(backend="github", repo="acme/x", tag="v1", assets={"k": "x-1.tgz"})
    assert a.identity_key() != b.identity_key()
    assert a.identity_key() == c.identity_key()


def test_cache_version_includes_identity_for_github(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "version": "1.7.1",
                "install": {
                    "backend": "github",
                    "repo": "acme/t",
                    "assets": {"linux-x86_64": "t-1.7.1.tgz"},
                },
            },
            "p.yaml": {"cli": "p", "version": "1.0", "install": {"backend": "path"}},
        },
    )
    inst = Installer(cache_root=tmp_path / "c", registry=reg, backends=[PathBackend()])
    gh = inst._cache_version(reg.load("t"))
    assert gh.startswith("1.7.1-")
    assert len(gh) > len("1.7.1-")
    assert inst._cache_version(reg.load("p")) == "1.0"


def test_env_allow_reaches_install_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET", "leak")
    seen: dict[str, dict[str, str]] = {}

    class RecordingBackend:
        name = "rec"

        def resolve(
            self, manifest: object, *, version: str, env: dict[str, str], ctx: object
        ) -> tuple[str, dict[str, str]]:
            del manifest, version, ctx
            seen["env"] = dict(env)
            return "/bin/true", env

    reg = make_reg(tmp_path, {"t.yaml": {"cli": "t", "install": {"backend": "rec"}}})
    inst = Installer(cache_root=tmp_path / "c", registry=reg, backends=[RecordingBackend()])
    inst.resolve(reg.load("t"), env_allow=["KEEP"])
    assert "MY_SECRET" not in seen["env"]
    assert "PATH" in seen["env"]


def test_npm_install_uses_ignore_scripts_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import maina.backends.npm as npm_mod
    from maina.backends.npm import NpmBackend

    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(argv: list[str], **_kw: object) -> _Result:
        calls.append(list(argv))
        return _Result()

    monkeypatch.setattr(npm_mod.subprocess, "run", fake_run)
    NpmBackend._npm_install("/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"})
    assert "--ignore-scripts" in calls[0]
    calls.clear()
    NpmBackend._npm_install(
        "/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"}, allow_scripts=True
    )
    assert "--ignore-scripts" not in calls[0]


# --- review C: expressiveness + output.from_file + validation (U5, B7, B11-B13) ---


def test_pairs_type_emits_flag_key_value() -> None:
    schema = {"arg": {"flag": "--arg", "type": "pairs"}}
    argv = ArgParser(schema).build("jq", [], {"arg": {"x": "1", "y": "2"}})
    assert argv == ["jq", "--arg", "x", "1", "--arg", "y", "2"]
    argv2 = ArgParser(schema).build("jq", [], {"arg": [["a", "b"]]})
    assert argv2 == ["jq", "--arg", "a", "b"]


def test_separator_injects_literal_token() -> None:
    schema = {
        "n": {"flag": "--max-count", "joiner": "=", "type": "string"},
        "paths": {"positional": True, "repeat": True, "type": "path", "separator": "--"},
    }
    argv = ArgParser(schema).build("git", ["log"], {"n": "3", "paths": ["src"]})
    assert argv == ["git", "log", "--max-count=3", "--", "src"]


def test_join_typo_rejected_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "path"},
                "args": {"a": {"flag": "--a", "type": "string", "join": "comma"}},
            }
        },
    )
    with pytest.raises(ManifestError, match="join 'comma' not in"):
        reg.load("t")


def test_top_level_timeout_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path, {"t.yaml": {"cli": "t", "timeout": "soon", "install": {"backend": "path"}}}
    )
    with pytest.raises(ManifestError, match="timeout 'soon' must be a number"):
        reg.load("t")


def test_platforms_values_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "t", "platforms": ["Linux"], "install": {"backend": "path"}}},
    )
    with pytest.raises(ManifestError, match=r"platforms .* not in"):
        reg.load("t")


def test_output_from_file_used_as_parse_input(tmp_path: Path) -> None:
    work = tmp_path / "work"
    (work / "report").mkdir(parents=True)
    (work / "report" / "r.json").write_text('{"clones": 3}', encoding="utf-8")
    reg = make_reg(
        tmp_path,
        {
            "j.yaml": {
                "cli": "true",
                "install": {"backend": "path"},
                "parser": "json",
                "args": {"out": {"flag": "--out", "type": "string"}},
                "output": {"success_codes": [0], "from_file": "{out}/r.json"},
            }
        },
    )
    runner = ToolRunner(
        reg.load("j"),
        installer=path_installer(tmp_path, reg),
        parsers=ParserResolver(generals=builtin_generals()),
    )
    result = runner.run({"out": "report"}, cwd=str(work))
    assert result.parsed == {"clones": 3}
    assert result.meta["parsed_from"].endswith("report/r.json")


def test_cli_json_split_keeps_dashdash_tail() -> None:
    from maina.cli import split_at_dashdash

    head, tail = split_at_dashdash(["a=1", "--json", "--", "--json"])
    assert head == ["a=1", "--json"]
    assert tail == ["--json"]


# --- review C: robustness P2s (B4, B8, B9, B10) -------------------------------


def test_copy_raw_asset_rejects_unsupported_format(tmp_path: Path) -> None:
    from maina.backends.archive import ArchiveFetcher

    src = tmp_path / "tool.tar.bz2"
    src.write_bytes(b"BZh9fake")
    with pytest.raises(InstallError, match="unsupported asset format"):
        ArchiveFetcher.copy_raw_asset(src, tmp_path / "out", name="tool.tar.bz2")


def test_resolve_cache_root_is_absolute() -> None:
    import pathlib

    root = Runtime.resolve_cache_root(pathlib.Path("rel-cache-xyz"))
    assert root.is_absolute()
    root.rmdir()


def test_run_bad_cwd_raises_tool_exec_error(tmp_path: Path) -> None:
    from maina.core.errors import ToolExecError

    reg = make_reg(
        tmp_path,
        {
            "e.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(reg.load("e"), installer=path_installer(tmp_path, reg))
    with pytest.raises(ToolExecError, match="could not run"):
        runner.run({}, cwd=str(tmp_path / "nope-does-not-exist"))


def test_run_without_stdin_does_not_block(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "c.yaml": {
                "cli": "cat",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(reg.load("c"), installer=path_installer(tmp_path, reg))
    result = runner.run({}, timeout=5)
    assert result.ok
    assert result.raw == ""
