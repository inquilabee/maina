"""maina CLI: prepare, help, and run via Tool."""

from __future__ import annotations

import json
import shlex
import sys
from typing import TYPE_CHECKING, Annotated, Any

import typer

from maina.core.errors import (
    AbstractToolError,
    ArgError,
    InstallError,
    MainaError,
    ManifestError,
)
from maina.runtime import runtime
from maina.tool import Tool

if TYPE_CHECKING:
    from maina.core.result import ToolResult

app = typer.Typer(
    name="maina",
    help=(
        "Registry-first CLI tool runner.\n\n"
        "Use `maina run <tool_id> key=value ... [-- extra...]` to invoke a "
        "registered tool. Pass registry kwargs as key=value tokens (repeat a key "
        "for list values); a bare key sets that flag to true. Tokens after `--` "
        "become extra argv. `maina run <tool_id>` or `... --help` prints that "
        "tool's registry help. List ids with `maina list`."
    ),
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_enable=False,
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


def echo_typer_help(ctx: Any) -> None:
    """Print Typer/Click help for ``ctx`` (no registry tool catalog)."""
    typer.echo(ctx.get_help())


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    show_version: Annotated[
        bool, typer.Option("--version", help="Print the maina version and exit")
    ] = False,
) -> None:
    """Show Typer package help when no subcommand is given."""
    if show_version:
        from maina import __version__

        typer.echo(__version__)
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        echo_typer_help(ctx)
        raise typer.Exit(0)


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show the same help as ``maina`` / ``maina --help``."""
    parent = ctx.parent if ctx.parent is not None else ctx
    echo_typer_help(parent)
    raise typer.Exit(0)


@app.command("list")
def list_cmd(
    tool_id: Annotated[
        str | None,
        typer.Argument(help="Parent tool id whose children to list (default: all concrete)"),
    ] = None,
) -> None:
    """List callable tool ids (all concrete, or children of TOOL_ID).

    Nested ids use hyphens (``kubectl-get``, ``git-remote-add``). Abstract
    parents stay hidden from the default catalog but may be passed as TOOL_ID.
    """
    registry = runtime().registry
    ids = registry.list_commands(tool_id)
    if not ids:
        if tool_id is None and not registry.roots:
            typer.echo(
                "no manifests registered — set MAINA_REGISTRY=<dir> "
                "or call maina.register(rt, registry_dirs=[...])"
            )
        else:
            typer.echo("(none)")
        return
    for tid in ids:
        typer.echo(tid)


@app.command()
def prepare(
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="Tool ids to prepare (default: all non-abstract)"),
    ] = None,
    check: Annotated[
        bool,
        typer.Option("--check", help="Report install state; do not download or run mise"),
    ] = False,
) -> None:
    """Ensure mise runtimes and tool installs.

    Default prepares every concrete tool. Explicit abstract ids (e.g. ``base``,
    ``kubectl``) fail with a non-zero exit instead of a silent skip. With
    ``--check`` nothing is installed: each id prints ``satisfied`` or
    ``missing`` and the command exits non-zero if anything is missing.
    """
    installer = runtime().installer
    try:
        if check:
            satisfied, missing = installer.check_ids(tools)
            for tid in satisfied:
                typer.echo(f"satisfied: {tid}")
            for tid in missing:
                typer.echo(f"missing: {tid}")
            if missing:
                raise typer.Exit(1)
            return
        prepared = installer.prepare_ids(tools)
    except (ManifestError, InstallError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
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
        typer.Argument(help="Registry tool id (e.g. jq, kubectl-get, git-remote-add)"),
    ] = None,
) -> None:
    """Run a registered tool via Tool(tool_id).run(**kwargs).

    Pass registry kwargs as ``key=value`` tokens. Repeat a key for list values.
    A bare key sets that flag to true. Tokens after ``--`` become ``extra``.

    Nested tool ids use hyphens (``kubectl-get``, ``git-remote-add``).
    ``maina run <tool_id>`` and ``... --help`` print that tool's registry help.
    ``--dry-run`` prints the argv that would run and exits without running it.
    """
    resolved_id, tokens, wants_help = RunInvocation.resolve(tool_id, list(ctx.args))
    if resolved_id is None:
        echo_typer_help(ctx)
        raise typer.Exit(0)
    if wants_help or RunInvocation.is_empty(tokens):
        emit_tool_help(resolved_id)
        raise typer.Exit(0)
    # Only the head (before `--`) is maina's; `--json` after `--` is the tool's.
    head, tail = split_at_dashdash(tokens)
    maina_flags = {t for t in head if t in {"--json", "--dry-run"}}
    head = [t for t in head if t not in maina_flags]
    tokens = head if tail is None else [*head, "--", *tail]
    dispatch_run(
        resolved_id,
        tokens,
        as_json="--json" in maina_flags,
        dry_run="--dry-run" in maina_flags,
    )


def dispatch_run(resolved_id: str, tokens: list[str], *, as_json: bool, dry_run: bool) -> None:
    """Print the argv (``--dry-run``) or run the tool and emit its result."""
    if dry_run:
        typer.echo(shlex.join(dry_run_argv(resolved_id, tokens)))
        return
    result = run_tool_tokens(resolved_id, tokens)
    emit_result(result, as_json=as_json)
    if result.parse_error:
        echo_stderr(f"parse error: {result.parse_error}")
    if not result.ok:
        echo_stderr(result.stderr)
        # Docs contract: exit 1 = tool ran and failed; 2 = bad usage / unknown id.
        raise typer.Exit(1)
    # A parse failure under --json (the caller asked for parsed output and got
    # none) is itself a failure.
    if as_json and result.parsed is None and result.parse_error:
        raise typer.Exit(1)


def dry_run_argv(resolved_id: str, tokens: list[str]) -> list[str]:
    """Build the argv a run would execute (argv[0] is the bare ``cli`` name)."""
    try:
        tool = Tool(resolved_id)
        kwargs, extra = RunArgParser.parse(tokens, flag_names=flag_names_for(tool))
        return tool.argv(extra=extra, **kwargs)
    except (ManifestError, AbstractToolError, ArgError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def flag_names_for(tool: Tool) -> frozenset[str]:
    return frozenset(name for name, spec in tool.args.items() if spec.type == "flag")


def run_tool_tokens(resolved_id: str, tokens: list[str]) -> ToolResult:
    """Build argv from CLI tokens and run the tool, mapping errors to exit codes."""
    try:
        tool = Tool(resolved_id)
        kwargs, extra = RunArgParser.parse(tokens, flag_names=flag_names_for(tool))
        return tool.run(extra=extra, **kwargs)
    except (ManifestError, AbstractToolError, ArgError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except MainaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def echo_stderr(text: str) -> None:
    if text:
        sys.stderr.write(text if text.endswith("\n") else f"{text}\n")


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
        """True only when nothing was passed at all.

        A bare ``maina run <id>`` prints help; ``maina run <id> --`` explicitly
        runs the tool with no arguments (for surfaces with no required args).
        """
        return not tokens


class RunArgParser:
    """Parse CLI tokens into Tool.run kwargs and optional extra argv."""

    @classmethod
    def parse(
        cls,
        tokens: list[str],
        *,
        flag_names: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, Any], list[str] | None]:
        """Split tokens into kwargs and optional extra passthrough after ``--``.

        ``true``/``yes``/``1``/``0``/… are coerced to booleans only for args the
        manifest declares as ``type: flag``; every other value stays a string.
        ``key:=value`` forces the literal string regardless.
        """
        kw_tokens, extra = split_at_dashdash(tokens)
        kwargs: dict[str, Any] = {}
        for token in kw_tokens:
            if not token:
                continue
            if ":=" in token:
                key, _, value = token.partition(":=")
                cls._require_key(token, key)
                cls._assign(kwargs, key, value=value)
            elif "=" in token:
                key, _, value = token.partition("=")
                cls._require_key(token, key)
                cls._assign(kwargs, key, value=cls._coerce(value) if key in flag_names else value)
            else:
                cls._assign(kwargs, token, value=True)
        return kwargs, extra

    @staticmethod
    def _require_key(token: str, key: str) -> None:
        if not key:
            raise typer.BadParameter(f"invalid run token: {token!r}")

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
        return (
            True
            if lowered in {"true", "yes", "1"}
            else (False if lowered in {"false", "no", "0"} else value)
        )


def emit_result(result: ToolResult, *, as_json: bool = False) -> None:
    """Print the tool's stdout as it produced it, or ``--json`` parsed output."""
    if as_json:
        sys.stdout.write(json.dumps(result.parsed, indent=2, default=str) + "\n")
        return
    if raw := result.raw:
        sys.stdout.write(raw if raw.endswith("\n") else f"{raw}\n")


def main(argv: list[str] | None = None) -> int:
    """Console entry point. Returns process exit code."""
    raw = list(sys.argv[1:] if argv is None else argv)
    RAW_ARGV.capture(raw)
    try:
        result = app(args=raw, standalone_mode=False)
    except typer.Exit as exc:
        code = exc.exit_code
        return 0 if code is None else int(code)
    except MainaError as exc:
        # A bad manifest, unknown id, or install failure is a one-line message,
        # not a stack trace.
        typer.echo(str(exc), err=True)
        return 2
    except Exception as exc:
        # typer vendors its own click fork, so the raised class is not
        # ``click.UsageError``; match by shape/name and re-raise anything else.
        if not is_usage_error(exc):
            raise
        typer.echo(usage_message(exc), err=True)
        return 2
    finally:
        RAW_ARGV.clear()
    return 0 if result is None else int(result)


def is_usage_error(exc: BaseException) -> bool:
    """True for a Click/typer usage error (bad option, missing/extra arg).

    Matched by class name because typer vendors its own click fork, so the
    raised class is not ``click.UsageError`` from the ``click`` package.
    """
    names = {cls.__name__ for cls in type(exc).__mro__}
    return not names.isdisjoint({"UsageError", "BadParameter", "NoSuchOption", "ClickException"})


def usage_message(exc: BaseException) -> str:
    fmt = getattr(exc, "format_message", None)
    return fmt() if callable(fmt) else str(exc)


if __name__ == "__main__":
    sys.exit(main())
