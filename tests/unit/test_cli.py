"""Tests for the Typer `maina` CLI app (list / run / prepare / validate / ...)."""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest
from typer.testing import CliRunner

import maina
from helpers import write_yaml
from maina.backends import PathBackend
from maina.cli import app, main
from maina.core.result import ToolResult
from maina.runtime import Runtime
from maina.tool import Tool

runner = CliRunner()


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


def test_cli_run_no_args_help_hints_at_double_dash(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "noargs.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(
        root / "needy.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "args": {"msg": {"positional": True, "required": True}},
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    hint = runner.invoke(app, ["run", "noargs"])
    assert hint.exit_code == 0
    assert "maina run noargs --" in hint.stdout
    needy = runner.invoke(app, ["run", "needy"])
    assert "no required arguments" not in needy.stdout


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


def test_cli_list_finds_grandchild_with_no_manifest_at_the_intermediate_level(
    tmp_path: Any,
) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "git" / "git.yaml", {"cli": "git", "abstract": True})
    write_yaml(
        root / "git" / "foo" / "bar.yaml",
        {"extends": "git", "subcommand": "bar", "install": {"backend": "path"}},
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["list", "git"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "git-foo-bar"
    assert result.stderr == ""


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
        timeout: float | None = None,
        cwd: str | None = None,
        stdin: str | None = None,
        env_allow: tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        del timeout, cwd, stdin, env_allow
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


def test_cli_run_arg_named_like_a_run_param_reaches_argv(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "prettierish.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "args": {
                "path": {"positional": True, "required": True},
                "parser": {"flag": "--parser"},
            },
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))

    dry = runner.invoke(app, ["run", "prettierish", "path=x", "parser=babel", "--dry-run"])
    assert dry.exit_code == 0
    assert dry.stdout.strip() == "echo --parser babel x"

    real = runner.invoke(app, ["run", "prettierish", "path=x", "parser=babel"])
    assert real.exit_code == 0
    assert real.stdout.strip() == "--parser babel x"


def test_cli_run_timeout_arg_does_not_collide_with_timeout_flag(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "collide.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "args": {"timeout": {"flag": "--timeout"}}},
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["run", "collide", "timeout=9"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "--timeout 9"


def test_cli_run_help_after_extra_separator_does_not_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(
        self: Tool,
        *,
        extra: str | list[str] | None = None,
        timeout: float | None = None,
        cwd: str | None = None,
        stdin: str | None = None,
        env_allow: tuple[str, ...] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        del timeout, cwd, stdin, env_allow
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


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()
    assert "maina" not in result.stdout.lower() or result.stdout.strip()[0].isdigit()


def test_cli_bad_manifest_is_one_line_not_traceback(
    tmp_path: Any, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    reg = tmp_path / "reg"
    reg.mkdir()
    (reg / "broke.yaml").write_text("cli: [oops\n", encoding="utf-8")
    monkeypatch.setenv("MAINA_REGISTRY", str(reg))
    maina.use(Runtime.preferred())
    # `list` skips the broken manifest — rendered as a plain `warning:` line
    # (not a raw Python .../loader.py:N: RuntimeWarning: ... dump) rather
    # than aborting.
    assert main(["list"]) == 0
    err = capsys.readouterr().err
    assert err.startswith("warning: skipping 'broke':")
    assert "loader.py" not in err
    # Naming the broken id explicitly is still a one-line error, exit 2.
    assert main(["run", "broke"]) == 2


def test_cli_run_dry_run_prints_argv_and_skips_execution() -> None:
    dry = runner.invoke(app, ["run", "jq", "--dry-run", "filter=.name", "raw"])
    assert dry.exit_code == 0, dry.stdout + dry.stderr
    # argv[0] is the bare cli name (a real run resolves it to the cached binary).
    assert dry.stdout.strip() == "jq -r .name"


def test_cli_describe_json_and_summary() -> None:
    import json as _json

    j = runner.invoke(app, ["describe", "jq", "--json"])
    assert j.exit_code == 0
    data = _json.loads(j.stdout)
    assert data["id"] == "jq"
    assert data["install"]["backend"] == "github"
    summary = runner.invoke(app, ["describe", "jq"])
    assert summary.exit_code == 0
    assert "jq" in summary.stdout
    assert "filter" in summary.stdout


def test_cli_schema_emits_valid_json_schema() -> None:
    import json as _json

    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0
    doc = _json.loads(result.stdout)
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert "cli" in doc["properties"]
    assert doc["$defs"]["install"]["properties"]["backend"]["examples"]


def test_cli_run_timeout_and_cwd_opts(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "sleep.yaml",
        {
            "cli": "sleep",
            "install": {"backend": "path"},
            "args": {"s": {"positional": True, "type": "string", "required": True}},
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root]))
    result = runner.invoke(app, ["run", "sleep", "--timeout=0.2", "s=10"])
    assert result.exit_code == 1
    assert "timed out" in (result.stdout + result.stderr).lower()


def test_cli_run_dotted_pairs_token(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "dkr.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "args": {"env": {"flag": "-e", "type": "pairs", "joiner": "="}},
        },
    )
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root]))
    dry = runner.invoke(app, ["run", "dkr", "--dry-run", "env.K=V", "env.A=B"])
    assert dry.exit_code == 0, dry.stdout + dry.stderr
    assert dry.stdout.strip() == "echo -e K=V -e A=B"


def test_cli_run_maina_flag_before_tool_id() -> None:
    # `--dry-run` written before the id must not be parsed as the id.
    dry = runner.invoke(app, ["run", "--dry-run", "jq", "filter=.name", "raw"])
    assert dry.exit_code == 0, dry.stdout + dry.stderr
    assert dry.stdout.strip() == "jq -r .name"


def test_cli_run_surfaces_parse_error(tmp_path: Any) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "j.yaml",
        {
            "cli": "echo",
            "install": {"backend": "path"},
            "parser": "json",
            "args": {"msg": {"positional": True, "type": "string", "required": True}},
        },
    )
    rt = Runtime.preferred(cache_root=tmp_path / "cache", registry_dirs=[root])
    maina.use(rt)
    result = runner.invoke(app, ["run", "j", "msg=not-json", "--json"])
    assert result.exit_code == 1
    assert "parse error" in result.stderr


def test_cli_run_no_arg_tool_with_dashdash(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bare ``run noargs`` prints help; ``run noargs --`` explicitly runs it.

    Exercised through ``main()``, not ``CliRunner.invoke(app, ...)``: only
    ``main()`` captures raw argv before Click strips ``--`` from ``ctx.args``.
    ``CliRunner`` calls the Typer app directly, bypassing that capture — Click
    then has already dropped the ``--`` with no way to tell "nothing after
    run noargs" apart from "an explicit, empty --" (see
    RunInvocation.recover_separator_tokens). A CliRunner-based version of this
    test would pass for the wrong reason: exit_code == 0 from the help path,
    not from the tool actually running.
    """
    root = tmp_path / "reg"
    write_yaml(
        root / "noargs.yaml",
        {"cli": "echo", "args": {"msg": {"positional": True, "default": "ran"}}},
    )
    rt = Runtime(cache_root=tmp_path / "cache")
    maina.register(rt, registry_dirs=[root], installers=[PathBackend()])
    maina.use(rt)
    assert main(["run", "noargs"]) == 0
    assert "Arguments:" in capsys.readouterr().out
    assert main(["run", "noargs", "--"]) == 0
    assert capsys.readouterr().out.strip() == "ran"


def test_cli_run_passes_the_tools_stderr_through_on_success(tmp_path: Any) -> None:
    """A linter's warnings and a deprecation notice are the reason a tool writes
    to stderr; a successful exit is no reason for maina to swallow them."""
    root = tmp_path / "reg"
    write_yaml(root / "sh.yaml", {"cli": "sh", "args": {"c": {"flag": "-c", "required": True}}})
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    result = runner.invoke(app, ["run", "sh", "c=echo OUT; echo ERRLINE >&2"])
    assert result.exit_code == 0
    assert "OUT" in result.stdout
    assert "ERRLINE" in result.stderr


def test_cli_run_reads_this_process_stdin_only_with_the_flag(tmp_path: Any) -> None:
    """``maina run`` looks like a general-purpose runner, so piping into it must
    work — but only on request, so a tool that reads stdin never blocks on a
    terminal by default."""
    root = tmp_path / "reg"
    write_yaml(root / "catt.yaml", {"cli": "cat"})
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))

    piped = runner.invoke(app, ["run", "catt", "--stdin", "--"], input="hello-from-pipe\n")
    assert piped.exit_code == 0
    assert "hello-from-pipe" in piped.stdout

    ignored = runner.invoke(app, ["run", "catt", "--"], input="hello-from-pipe\n")
    assert ignored.exit_code == 0
    assert "hello-from-pipe" not in ignored.stdout


def test_cli_run_env_allow_reaches_the_tool(tmp_path: Any) -> None:
    """``docs/security.md`` presents ``env_allow`` as the way to keep secrets out
    of a third-party binary; it must have a CLI spelling."""
    root = tmp_path / "reg"
    write_yaml(root / "sh.yaml", {"cli": "sh", "args": {"c": {"flag": "-c", "required": True}}})
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))
    os.environ["MAINA_TEST_TRIMMED"] = "leaked"
    os.environ["MAINA_TEST_KEEP"] = "kept"
    try:
        script = "echo ${MAINA_TEST_TRIMMED:-gone} $MAINA_TEST_KEEP"
        trimmed = runner.invoke(app, ["run", "sh", "--env-allow=MAINA_TEST_KEEP", f"c={script}"])
        assert trimmed.exit_code == 0
        assert "gone kept" in trimmed.stdout
    finally:
        del os.environ["MAINA_TEST_TRIMMED"], os.environ["MAINA_TEST_KEEP"]


def test_extra_passthrough_survives_when_another_app_mounts_maina(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An app that mounts maina's commands under its own name owns the entry
    point and never calls ``main()``. Click has already dropped the ``--`` by
    then, so without recovery the tool's own options are parsed as kwargs and
    ``maina run t -- -n`` fails with "'-n' is not a flag"."""
    import typer

    root = tmp_path / "reg"
    write_yaml(root / "echoer.yaml", {"cli": "echo", "args": {"words": {"positional": True}}})
    maina.use(Runtime.preferred(cache_root=tmp_path / "c", registry_dirs=[root], use_env=False))

    host = typer.Typer(add_completion=False, pretty_exceptions_enable=False)
    host.add_typer(app, name="tools")
    monkeypatch.setattr(
        sys, "argv", ["doctor", "tools", "run", "echoer", "--dry-run", "words=a", "--", "-n"]
    )
    result = CliRunner().invoke(
        host, ["tools", "run", "echoer", "--dry-run", "words=a", "--", "-n"]
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "echo a -n"
