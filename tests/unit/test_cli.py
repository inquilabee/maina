"""Tests for Tool.__str__ / help and Typer CLI run parsing."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

import maina
from helpers import write_yaml
from maina.backends import PathBackend
from maina.cli import RunArgParser, RunInvocation, app, main
from maina.core.result import ToolResult
from maina.runtime import Runtime
from maina.tool import Tool

runner = CliRunner()


def test_str_bound_tool_owns_help_block() -> None:
    text = str(Tool("jq"))
    assert text.startswith("Tool: jq")
    assert "cli: jq" in text
    assert "abstract:" not in text
    assert "Arguments:" in text
    assert "filter:" in text


def test_str_abstract_tool_shows_abstract_true() -> None:
    text = str(Tool("kubectl"))
    assert text.startswith("Tool: kubectl")
    assert "abstract: true" in text
    assert "abstract: False" not in text
    assert "abstract: None" not in text


def test_str_shellcheck_includes_arg_descriptions() -> None:
    text = str(Tool("shellcheck"))
    assert "Tool: shellcheck" in text
    assert "cli: shellcheck" in text
    assert "path:" in text
    assert "Script file" in text


def test_str_unbound_is_library_help() -> None:
    text = str(Tool())
    assert "Registered tools:" in text
    assert "import maina" in text
    assert "maina run" not in text
    assert "shellcheck" in text
    bullets = [
        line.strip().removeprefix("- ").strip()
        for line in text.split("Registered tools:", 1)[1].splitlines()
        if line.strip().startswith("-")
    ]
    assert "kubectl-get" in bullets
    assert "kubectl" not in bullets
    assert "base" not in bullets
    assert "git" not in bullets


def test_help_returns_str(capsys: pytest.CaptureFixture[str]) -> None:
    tool = Tool("jq")
    returned = tool.help()
    captured = capsys.readouterr()
    assert returned == str(tool)
    assert captured.out == ""


def test_run_arg_parser_kwargs_and_extra() -> None:
    kwargs, extra = RunArgParser.parse(
        ["filter=.", "null_input", "path=a", "path=b", "--", "-c", "--arg", "x", "y"]
    )
    assert kwargs == {
        "filter": ".",
        "null_input": True,
        "path": ["a", "b"],
    }
    assert extra == ["-c", "--arg", "x", "y"]


def test_run_arg_parser_bool_coercion() -> None:
    kwargs, extra = RunArgParser.parse(["silent=true", "raw=false"])
    assert kwargs == {"silent": True, "raw": False}
    assert extra is None


def test_run_invocation_strips_help_before_extra() -> None:
    tool_id, tokens, wants_help = RunInvocation.parse("shellcheck", ["--help", "--", "--help"])
    assert tool_id == "shellcheck"
    assert wants_help is True
    assert tokens == ["--", "--help"]


def test_cli_root_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "prepare" in result.stdout
    assert "run" in result.stdout


def test_cli_run_help_without_tool() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "tool_id" in result.stdout or "TOOL_ID" in result.stdout
    assert "Top-level tools:" not in result.stdout


def test_cli_bare_shows_typer_help() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "prepare" in result.stdout
    assert "run" in result.stdout
    assert "list" in result.stdout
    assert "Top-level tools:" not in result.stdout


def test_cli_list_top_level() -> None:
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "shellcheck" in lines
    assert "jq" in lines
    assert "kubectl-get" in lines
    assert "kubectl-apply" in lines
    assert "git-remote-add" in lines
    assert "base" not in lines
    assert "kubectl" not in lines
    assert "git" not in lines
    assert "Top-level tools:" not in result.stdout


def test_cli_list_children() -> None:
    result = runner.invoke(app, ["list", "kubectl"])
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert "kubectl-get" in lines
    assert "kubectl-apply" in lines
    assert "kubectl/get" not in lines


def test_cli_list_children_hyphen_parent() -> None:
    result = runner.invoke(app, ["list", "git-remote"])
    assert result.exit_code == 0
    assert "git-remote-add" in result.stdout.splitlines()


def test_cli_run_accepts_hyphen_id() -> None:
    result = runner.invoke(app, ["run", "kubectl-get"])
    assert result.exit_code == 0
    assert "Tool: kubectl-get" in result.stdout


def test_cli_run_tool_help_flag() -> None:
    result = runner.invoke(app, ["run", "shellcheck", "--help"])
    assert result.exit_code == 0
    assert "Tool: shellcheck" in result.stdout
    assert "cli: shellcheck" in result.stdout
    assert "Arguments:" in result.stdout
    assert "path:" in result.stdout
    assert "Script file" in result.stdout
    assert "Usage: maina run [OPTIONS]" not in result.stdout


def test_cli_run_tool_help_short_flag() -> None:
    result = runner.invoke(app, ["run", "shellcheck", "-h"])
    assert result.exit_code == 0
    assert "Tool: shellcheck" in result.stdout


def test_cli_run_tool_bare_prints_help() -> None:
    result = runner.invoke(app, ["run", "shellcheck"])
    assert result.exit_code == 0
    assert "Tool: shellcheck" in result.stdout
    assert "Arguments:" in result.stdout
    assert "missing required" not in result.stdout
    assert "missing required" not in result.stderr


def test_cli_help_subcommand() -> None:
    result = runner.invoke(app, ["help"])
    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "prepare" in result.stdout
    assert "Top-level tools:" not in result.stdout


def test_cli_run_unknown_tool() -> None:
    result = runner.invoke(app, ["run", "definitely-missing-tool"])
    assert result.exit_code == 2


def test_cli_run_with_kwargs_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str | None, dict[str, Any]]] = []

    def fake_run(
        self: Tool,
        *,
        extra: str | list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        calls.append((self.id, {"extra": extra, **kwargs}))
        return ToolResult(
            tool=self.id or "",
            ok=True,
            exit_code=0,
            raw="ok\n",
            stderr="",
            command=["shellcheck", "script.sh"],
            parsed="ok\n",
        )

    monkeypatch.setattr(Tool, "run", fake_run)
    result = runner.invoke(app, ["run", "shellcheck", "path=script.sh", "format=gcc"])
    assert result.exit_code == 0
    assert calls == [("shellcheck", {"extra": None, "path": "script.sh", "format": "gcc"})]
    assert "ok" in result.stdout


def test_cli_run_help_after_extra_separator_does_not_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(
        self: Tool,
        *,
        extra: str | list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        calls.append({"extra": extra, **kwargs})
        return ToolResult(
            tool=self.id or "",
            ok=True,
            exit_code=0,
            raw="",
            stderr="",
            command=["shellcheck"],
        )

    monkeypatch.setattr(Tool, "run", fake_run)
    # main() captures raw argv so ``--`` survives Click stripping.
    assert main(["run", "shellcheck", "path=x.sh", "--", "--help"]) == 0
    assert calls == [{"extra": ["--help"], "path": "x.sh"}]


def test_main_returns_int_for_help() -> None:
    assert main(["help"]) == 0
    assert main(["--help"]) == 0
    assert main(["run", "shellcheck", "--help"]) == 0
    assert main(["run", "definitely-missing-tool"]) == 2


def test_cli_prepare_path_tool(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "trueish.yaml",
        {"cli": "true", "install": {"backend": "path"}},
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "trueish"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "prepared: trueish" in result.stdout


def test_cli_prepare_check_reports_state(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    # `true` is on PATH → satisfied; a bogus cli → missing.
    write_yaml(root / "trueish.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(
        root / "nope.yaml",
        {"cli": "definitely-not-a-real-binary", "install": {"backend": "path"}},
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "--check"])
    assert result.exit_code == 1, result.stdout + result.stderr
    assert "satisfied: trueish" in result.stdout
    assert "missing: nope" in result.stdout

    ok = runner.invoke(app, ["prepare", "trueish", "--check"])
    assert ok.exit_code == 0, ok.stdout + ok.stderr
    assert ok.stdout.strip() == "satisfied: trueish"


def test_cli_prepare_rejects_explicit_abstract(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"cli": "base", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "true",
            "extends": "base",
            "abstract": False,
            "install": {"backend": "path"},
        },
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare", "base"])
    assert result.exit_code == 2
    assert "cannot prepare abstract" in (result.stdout + result.stderr)


def test_cli_prepare_skips_abstract(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"cli": "base", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "true",
            "extends": "base",
            "abstract": False,
            "install": {"backend": "path"},
        },
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    result = runner.invoke(app, ["prepare"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "prepared: child" in result.stdout
    assert "prepared: base" not in result.stdout
