"""ToolRunner smoke tests: PathBackend, parsers, cwd, timeout, success_codes."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from helpers import fixture_runtime, write_yaml
from maina.backends import PathBackend
from maina.core.errors import ToolTimeoutError
from maina.core.parsers import ParserResolver, builtin_generals
from maina.core.registry import Registry
from maina.installer import Installer
from maina.runner import ToolRunner

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.protocols import LanguageRuntimeProvider


@dataclass
class FakeLanguage:
    cache_root: Path

    def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
        del runtime, version
        return self.cache_root

    def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
        del runtime, version
        return {"PATH": os.environ.get("PATH", "")}


def path_installer(tmp_path: Path, registry: Registry) -> Installer:
    cache = tmp_path / "cache"
    return Installer(
        cache_root=cache,
        language=FakeLanguage(cache),
        registry=registry,
        backends=[PathBackend()],
    )


def test_toolrunner_path_backend_echo_success(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "args": {"msg": {"positional": True, "type": "string", "required": True}},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(registry.load("echo"), installer=path_installer(tmp_path, registry))
    result = runner.run({"msg": "hello-maina"})
    assert result.ok
    assert result.exit_code == 0
    assert "hello-maina" in result.raw
    assert result.command[0].endswith("echo") or result.command[0] == "echo"


def test_toolrunner_success_codes_accept_nonzero(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "falsey.yaml",
        {
            "cli": "false",
            "install": {"backend": "path"},
            "output": {"success_codes": [1]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(registry.load("falsey"), installer=path_installer(tmp_path, registry))
    result = runner.run({})
    assert result.ok
    assert result.exit_code == 1


def test_toolrunner_applies_json_parser(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echojson.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "parser": "json",
            "args": {"msg": {"positional": True, "type": "string", "required": True}},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(
        registry.load("echojson"),
        installer=path_installer(tmp_path, registry),
        parsers=ParserResolver(generals=builtin_generals()),
    )
    result = runner.run({"msg": '{"a": 1}'})
    assert result.ok
    assert result.parsed == {"a": 1}


def test_toolrunner_cwd(tmp_path: Path) -> None:
    work = tmp_path / "cwd"
    work.mkdir()
    marker = work / "here.txt"
    marker.write_text("ok", encoding="utf-8")
    root = tmp_path / "reg"
    write_yaml(
        root / "pwd.yaml",
        {
            "cli": "pwd",
            "install": {"backend": "path"},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(registry.load("pwd"), installer=path_installer(tmp_path, registry))
    result = runner.run({}, cwd=str(work))
    assert result.ok
    assert result.raw.strip() == str(work.resolve())


def test_toolrunner_timeout_kwarg(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "sleep.yaml",
        {
            "cli": "sleep",
            "install": {"backend": "path"},
            "args": {"seconds": {"positional": True, "type": "string", "required": True}},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(registry.load("sleep"), installer=path_installer(tmp_path, registry))
    with pytest.raises(ToolTimeoutError, match="timed out") as excinfo:
        runner.run({"seconds": "30"}, timeout=0.05)
    assert excinfo.value.tool == "sleep"
    assert excinfo.value.timeout == pytest.approx(0.05)


def test_toolrunner_text_false_returns_bytes_and_skips_parser(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {
            "cli": "printf",
            "install": {"backend": "path"},
            "parser": "json",
            "args": {"s": {"positional": True, "type": "string", "required": True}},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(
        registry.load("t"),
        installer=path_installer(tmp_path, registry),
        parsers=ParserResolver(generals=builtin_generals()),
    )
    result = runner.run({"s": "not-json"}, text=False)
    assert result.raw_bytes == b"not-json"
    assert result.raw == "not-json"  # lossy text view
    assert result.parsed is None
    assert result.parse_error is None  # parser never ran
    assert result.to_dict()["raw"] == "not-json"


def test_toolrunner_non_numeric_timeout_kwarg_raises_arg_error(tmp_path: Path) -> None:
    from maina.core.errors import ArgError

    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", {"cli": "echo", "install": {"backend": "path"}})
    registry = Registry(root)
    runner = ToolRunner(registry.load("t"), installer=path_installer(tmp_path, registry))
    bad_timeout: Any = "soon"
    with pytest.raises(ArgError, match="number of seconds"):
        runner.run({}, timeout=bad_timeout)


def test_toolrunner_executor_records_cwd(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["argv"] = argv
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(argv, 0, stdout="x\n", stderr="")

    # PathBackend still needs a real echo on PATH for resolve.
    runner = ToolRunner(
        registry.load("echo"),
        installer=path_installer(tmp_path, registry),
        executor=fake_run,
    )
    cwd = str(tmp_path / "work")
    result = runner.run({}, cwd=cwd)
    assert result.ok
    assert seen["cwd"] == cwd
    assert seen["argv"][0].endswith("echo") or seen["argv"][0] == "echo"


def test_family_parser_apply_via_fixture() -> None:
    rt = fixture_runtime()
    manifest = rt.registry.load("git-remote-add")
    assert manifest.parser == "add.py"
    assert manifest.parser_path is not None
    from maina.core.result import ToolResult

    # apply() skips parsers when raw is empty; non-empty raw exercises family parse().
    result = ToolResult(
        tool=manifest.id,
        ok=True,
        exit_code=0,
        raw="note: unused\n",
        stderr="",
        command=["git", "remote", "add"],
    )
    out = rt.parsers.bind(
        parser_ref=manifest.parser,
        parser_path=manifest.parser_path,
    ).apply(result)
    assert out.parsed == {"ok": True, "raw": "note: unused\n"}


def test_fake_language_protocol(tmp_path: Path) -> None:
    lang: LanguageRuntimeProvider = FakeLanguage(tmp_path)
    assert lang.ensure("node", "20") == tmp_path


def test_toolrunner_env_allow_trims_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPER_SECRET", "leak-me")
    monkeypatch.setenv("KEEP_ME", "ok")
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "output": {"success_codes": [0]}},
    )
    registry = Registry(root)
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = ToolRunner(
        registry.load("echo"),
        installer=path_installer(tmp_path, registry),
        executor=fake_run,
    )
    runner.run({}, env_allow=["KEEP_ME"])
    env = seen["env"]
    assert "PATH" in env
    assert env.get("KEEP_ME") == "ok"
    assert "SUPER_SECRET" not in env

    # Default (no env_allow) forwards the full environment.
    runner.run({})
    assert "SUPER_SECRET" in seen["env"]


def test_toolrunner_per_call_env_overlay(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "env": {"WINS": "manifest"}},
    )
    registry = Registry(root)
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = ToolRunner(
        registry.load("echo"), installer=path_installer(tmp_path, registry), executor=fake_run
    )
    runner.run({}, env_allow=[], env={"CALL_VAR": "1", "WINS": "call"})
    assert seen["env"]["CALL_VAR"] == "1"
    assert seen["env"]["WINS"] == "manifest"  # manifest env: still wins a collision


def test_toolrunner_manifest_env_survives_env_allow(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "env": {"TOOL_FLAG": "on"},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    seen: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    runner = ToolRunner(
        registry.load("echo"),
        installer=path_installer(tmp_path, registry),
        executor=fake_run,
    )
    runner.run({}, env_allow=[])
    assert seen["env"]["TOOL_FLAG"] == "on"


def test_toolrunner_bad_kwarg_raises_before_install(tmp_path: Path) -> None:
    from maina.core.errors import ArgError

    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {"cli": "t", "install": {"backend": "github", "repo": "acme/t"}},
    )
    registry = Registry(root)
    calls: list[str] = []

    class ExplodingInstaller(Installer):
        def resolve(self, manifest: Any, *, env_allow: Any = None) -> Any:
            del manifest, env_allow
            calls.append("resolve")
            raise AssertionError("install must not run when a kwarg is invalid")

    runner = ToolRunner(
        registry.load("t"),
        installer=ExplodingInstaller(
            cache_root=tmp_path / "cache",
            language=FakeLanguage(tmp_path / "cache"),
            registry=registry,
            backends=[PathBackend()],
        ),
    )
    with pytest.raises(ArgError, match="unknown"):
        runner.run({"nope": "1"})
    assert calls == []


def test_from_file_template_rejects_attribute_access(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "parser": "json",
            "output": {"success_codes": [0], "from_file": "{cwd.__class__}/r.json"},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(
        registry.load("t"),
        installer=path_installer(tmp_path, registry),
        parsers=ParserResolver(generals=builtin_generals()),
    )
    result = runner.run({})
    assert result.parse_error is not None
    assert "attribute or item access" in result.parse_error
