"""pyporter CLI: prepare, help, and run via Tool."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Annotated, Any

import typer

from pyporter.errors import AbstractToolError, ArgError, ManifestError, PorterError
from pyporter.runtime import runtime
from pyporter.tool import Tool

if TYPE_CHECKING:
    from pyporter.result import ToolResult

app = typer.Typer(
    name="pyporter",
    help="Registry-first CLI tool runner",
    add_completion=False,
    no_args_is_help=False,
)


def split_at_dashdash(tokens: list[str]) -> tuple[list[str], list[str] | None]:
    """Split tokens on the first ``--``; return (head, tail_without_sep).

    When ``--`` is absent, returns ``(tokens, None)``. When present, the
    separator itself is omitted from both sides (tail may be empty).
    """
    if "--" not in tokens:
        return tokens, None
    sep = tokens.index("--")
    return tokens[:sep], tokens[sep + 1 :]


@app.callback(invoke_without_command=True)
def root(ctx: typer.Context) -> None:
    """Show package help when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        typer.echo(Tool().help())


@app.command("help")
def help_cmd() -> None:
    """Show package help."""
    typer.echo(Tool().help())


@app.command()
def prepare(
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="Tool ids to prepare (default: all non-abstract)"),
    ] = None,
) -> None:
    """Ensure mise runtimes and tool installs."""
    prepared = runtime().installer.prepare_ids(list(tools) if tools else None)
    for tid in prepared:
        typer.echo(f"prepared: {tid}")


@app.command(
    context_settings={
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
    },
)
def run(
    ctx: typer.Context,
    tool_id: Annotated[
        str | None,
        typer.Argument(help="Registry tool id (e.g. jq, kubectl/get, git/remote/add)"),
    ] = None,
) -> None:
    """Run a registered tool via Tool(tool_id).run(**kwargs).

    Pass registry kwargs as ``key=value`` tokens. Repeat a key for list values.
    A bare key sets that flag to true. Tokens after ``--`` become ``extra``.

    ``pyporter run <tool_id>`` and ``... --help`` print that tool's registry help.
    """
    resolved_id, tokens, wants_help = RunInvocation.resolve(tool_id, list(ctx.args))
    if resolved_id is None:
        typer.echo(Tool.run_usage_help())
        raise typer.Exit(0)
    if wants_help or RunInvocation.is_empty(tokens):
        emit_tool_help(resolved_id)
        raise typer.Exit(0)

    kwargs, extra = RunArgParser.parse(tokens)
    try:
        result = Tool(resolved_id).run(extra=extra, **kwargs)
    except (ManifestError, AbstractToolError, ArgError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except PorterError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    emit_result(result)
    if not result.ok:
        if result.stderr:
            sys.stderr.write(
                result.stderr if result.stderr.endswith("\n") else f"{result.stderr}\n"
            )
        raise typer.Exit(result.exit_code if result.exit_code != 0 else 1)


def emit_tool_help(tool_id: str) -> None:
    """Print registry help for a tool id, or exit 2 if the id is unknown."""
    try:
        typer.echo(Tool(tool_id).help())
    except ManifestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


class RawArgv:
    """Capture process argv before Click drops the ``--`` separator."""

    def __init__(self) -> None:
        self._argv: list[str] | None = None

    def capture(self, argv: list[str]) -> None:
        """Store argv for later ``run`` token recovery."""
        self._argv = list(argv)

    def clear(self) -> None:
        """Drop the captured argv."""
        self._argv = None

    def after_command(self, command: str) -> list[str] | None:
        """Return tokens after ``command``, or None when unset / missing."""
        if self._argv is None:
            return None
        try:
            idx = self._argv.index(command)
        except ValueError:
            return None
        return self._argv[idx + 1 :]


RAW_ARGV = RawArgv()


class RunInvocation:
    """Normalize ``run`` argv: tool id, payload tokens, and help intent."""

    HELP_FLAGS = frozenset({"--help", "-h"})

    @classmethod
    def resolve(
        cls,
        tool_id: str | None,
        ctx_args: list[str],
    ) -> tuple[str | None, list[str], bool]:
        """Prefer raw argv (keeps ``--``); fall back to Click ``ctx.args``."""
        raw_after = RAW_ARGV.after_command("run")
        return cls.parse_raw(raw_after) if raw_after is not None else cls.parse(tool_id, ctx_args)

    @classmethod
    def parse_raw(cls, after_run: list[str]) -> tuple[str | None, list[str], bool]:
        """Parse tokens captured after the ``run`` command name."""
        return cls.parse(after_run[0], after_run[1:]) if after_run else (None, [], False)

    @classmethod
    def parse(
        cls,
        tool_id: str | None,
        tokens: list[str],
    ) -> tuple[str | None, list[str], bool]:
        """Return ``(tool_id, tokens, wants_help)`` with help flags stripped from the head."""
        if tool_id in cls.HELP_FLAGS:
            tool_id = None
        head, tail = split_at_dashdash(tokens)
        if tail is not None:
            wants_help = any(t in cls.HELP_FLAGS for t in head)
            head = [t for t in head if t not in cls.HELP_FLAGS]
            # Preserve ``--`` in the token stream for RunArgParser / is_empty.
            return tool_id, [*head, "--", *tail], wants_help
        # Click drops ``--``; only treat sole help flags as help requests.
        wants_help = all(t in cls.HELP_FLAGS for t in tokens) if tokens else False
        return tool_id, ([] if wants_help else tokens), wants_help

    @staticmethod
    def is_empty(tokens: list[str]) -> bool:
        """True when there are no kwargs and no passthrough after ``--``."""
        return not tokens or tokens == ["--"]


class RunArgParser:
    """Parse CLI tokens into Tool.run kwargs and optional extra argv."""

    @classmethod
    def parse(cls, tokens: list[str]) -> tuple[dict[str, Any], list[str] | None]:
        """Split tokens into kwargs and optional extra passthrough after ``--``."""
        kw_tokens, extra = split_at_dashdash(tokens)
        kwargs: dict[str, Any] = {}
        for token in kw_tokens:
            if not token:
                continue
            if "=" in token:
                key, _, value = token.partition("=")
                if not key:
                    raise typer.BadParameter(f"invalid run token: {token!r}")
                cls._assign(kwargs, key, value=cls._coerce(value))
            else:
                cls._assign(kwargs, token, value=True)
        return kwargs, extra

    @staticmethod
    def _assign(kwargs: dict[str, Any], key: str, *, value: Any) -> None:
        """Set or append a kwarg; repeated keys become lists."""
        if key not in kwargs:
            kwargs[key] = value
            return
        existing = kwargs[key]
        if isinstance(existing, list):
            existing.append(value)
        else:
            kwargs[key] = [existing, value]

    @staticmethod
    def _coerce(value: str) -> Any:
        """Coerce common boolean spellings; leave other values as strings."""
        lowered = value.lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
        return value


def emit_result(result: ToolResult) -> None:
    """Print parsed output when present, otherwise raw stdout."""
    payload = result.parsed if result.parsed is not None else result.raw
    if payload is None or payload == "":
        return
    if isinstance(payload, str):
        sys.stdout.write(payload if payload.endswith("\n") else f"{payload}\n")
        return
    sys.stdout.write(json.dumps(payload, indent=2, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Console entry point. Returns process exit code."""
    raw = list(sys.argv[1:] if argv is None else argv)
    RAW_ARGV.capture(raw)
    try:
        result = app(args=raw, standalone_mode=False)
    except typer.Exit as exc:
        code = exc.exit_code
        return 0 if code is None else int(code)
    finally:
        RAW_ARGV.clear()
    return 0 if result is None else int(result)


if __name__ == "__main__":
    sys.exit(main())
