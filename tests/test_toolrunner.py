"""ToolRunner smoke tests: PathBackend, parsers, cwd, timeout, success_codes."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from pyporter.backends import PathBackend
from pyporter.errors import InstallError
from pyporter.installer import Installer
from pyporter.parsers import ParserResolver, builtin_generals
from pyporter.registry import Registry
from pyporter.runner import ToolRunner
from pyporter.runtime import Runtime

from helpers import write_yaml

if TYPE_CHECKING:
    from pathlib import Path

    from pyporter.protocols import LanguageRuntimeProvider


@dataclass
class FakeLanguage:
    cache_root: Path

    def ensure(self, runtime: str, version: str) -> Path:
        del runtime, version
        return self.cache_root

    def env(self, runtime: str, version: str) -> dict[str, str]:
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
    result = runner.run({"msg": "hello-porter"})
    assert result.ok
    assert result.exit_code == 0
    assert "hello-porter" in result.raw
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
    with pytest.raises(InstallError, match="timed out"):
        runner.run({"seconds": "30"}, timeout=0.05)


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
    result = runner.run({}, cwd="/tmp")
    assert result.ok
    assert seen["cwd"] == "/tmp"
    assert seen["argv"][0].endswith("echo") or seen["argv"][0] == "echo"


def test_family_parser_apply_via_preferred() -> None:
    rt = Runtime.preferred()
    manifest = rt.registry.load("git/remote/add")
    assert manifest.parser == "add.py"
    assert manifest.parser_path is not None
    from pyporter.result import ToolResult

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
