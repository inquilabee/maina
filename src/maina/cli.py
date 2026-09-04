"""maina CLI: prepare, help, and run via Tool."""

from __future__ import annotations

import json
import shlex
import sys
from typing import TYPE_CHECKING, Annotated, Any

import typer

from maina.clitokens import (
    RAW_ARGV,
    RunArgParser,
    RunInvocation,
    RunOpts,
    split_at_dashdash,
)
from maina.core.errors import (
    AbstractToolError,
    ArgError,
    InstallError,
    MainaError,
    ManifestError,
)
from maina.core.platform import KNOWN_PLATFORM_KEYS
from maina.core.registry import build_manifest_schema
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
def validate(
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="Tool ids to validate (default: every manifest under the roots)"),
    ] = None,
) -> None:
    """Load every manifest (or the named ids) and report each problem without aborting.

    Checks the manifest loads, its ``parser:`` resolves, its ``install.backend``
    is registered, and its ``install.assets`` / ``sha256`` platform keys are
    real. Prints ``ok:`` / ``error:`` per id and exits non-zero if any failed.
    """
    rt = runtime()
    ids = tools or rt.registry.iter_ids()
    if not ids:
        typer.echo("no manifests registered")
        return
    failed = 0
    for tid in sorted(ids):
        problem = validate_one(rt, tid)
        if problem is None:
            typer.echo(f"ok: {tid}")
        else:
            typer.echo(f"error: {tid}: {problem}", err=True)
            failed += 1
    if failed:
        typer.echo(f"{failed} manifest(s) failed", err=True)
        raise typer.Exit(1)


def validate_one(rt: Any, tid: str) -> str | None:
    """A one-line problem string for *tid*, or ``None`` when it is fine."""
    try:
        install = Tool(tid, runtime=rt).manifest.install  # loads + checks parser
    except MainaError as exc:
        return str(exc)
    if install.backend not in rt.installer.backend_names:
        known = ", ".join(sorted(rt.installer.backend_names)) or "none"
        return f"install backend {install.backend!r} is not registered (known: {known})"
    if bad := sorted((set(install.assets) | set(install.sha256)) - KNOWN_PLATFORM_KEYS):
        return f"install.assets/sha256 platform keys not recognised: {', '.join(bad)}"
    return None


@app.command()
def describe(
    tool_id: Annotated[str, typer.Argument(help="Registry tool id")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Emit the raw JSON (default: a compact summary)")
    ] = False,
) -> None:
    """Print a tool's manifest as data — for docs, forms, or shell completion.

    ``--json`` emits exactly ``Tool(id).describe()``; without it, a human-readable
    summary of the same fields.
    """
    try:
        desc = Tool(tool_id).describe()
    except MainaError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if as_json:
        typer.echo(json.dumps(desc, indent=2, default=str))
        return
    typer.echo(f"{desc['id']}  (cli: {desc['cli']}, backend: {desc['install']['backend']})")
    for name, arg in desc["args"].items():
        slot = arg["flag"] or ("positional" if arg["positional"] else "?")
        req = " required" if arg["required"] else ""
        typer.echo(f"  {name}: {arg['type']} {slot}{req}")


@app.command()
def schema() -> None:
    """Print the manifest JSON Schema (Draft 2020-12).

    Pipe it to a file and point an editor at it with a header line::

        # yaml-language-server: $schema=./maina-manifest.schema.json
    """
    typer.echo(json.dumps(build_manifest_schema(), indent=2))


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
    force: Annotated[
        bool,
        typer.Option("--force", help="Delete cached installs for these tools first, then prepare"),
    ] = False,
) -> None:
    """Ensure mise runtimes and tool installs.

    Default prepares every concrete tool. Explicit abstract ids (e.g. ``base``,
    ``kubectl``) fail with a non-zero exit instead of a silent skip. With
    ``--check`` nothing is installed: each id prints ``satisfied`` or
    ``missing`` and the command exits non-zero if anything is missing.
    ``--force`` wipes the cached install for each named tool before preparing;
    it needs explicit ids and cannot be combined with ``--check``.
    """
    if force and check:
        raise typer.BadParameter("--force cannot be combined with --check (which installs nothing)")
    if force and not tools:
        raise typer.BadParameter("--force needs explicit tool ids ('maina clean' wipes the cache)")
    installer = runtime().installer
    try:
        if check:
            emit_check(installer, tools)
            return
        for cli in installer.clean_ids(tools) if force else []:
            typer.echo(f"cleaned: {cli}")
        prepared = installer.prepare_ids(tools)
    except (ManifestError, InstallError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for tid in prepared:
        typer.echo(f"prepared: {tid}")


def emit_check(installer: Any, tools: list[str] | None) -> None:
    """``prepare --check``: print each id's state and exit non-zero if any missing."""
    satisfied, missing = installer.check_ids(tools)
    for tid in satisfied:
        typer.echo(f"satisfied: {tid}")
    for tid in missing:
        typer.echo(f"missing: {tid}")
    if missing:
        raise typer.Exit(1)


@app.command()
def clean(
    tools: Annotated[
        list[str] | None,
        typer.Argument(help="Tool ids to clear from the cache (default: the whole tool cache)"),
    ] = None,
) -> None:
    """Delete cached tool installs so the next run re-downloads them."""
    removed = runtime().installer.clean_ids(tools)
    for cli in removed:
        typer.echo(f"cleaned: {cli}")
    if not removed:
        typer.echo("nothing to clean")


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
    opts, head = RunInvocation.split_maina_opts(head)
    tokens = head if tail is None else [*head, "--", *tail]
    dispatch_run(resolved_id, tokens, opts)


def dispatch_run(resolved_id: str, tokens: list[str], opts: RunOpts) -> None:
    """Print the argv (``--dry-run``) or run the tool and emit its result."""
    if opts.dry_run:
        typer.echo(shlex.join(dry_run_argv(resolved_id, tokens)))
        return
    result = run_tool_tokens(resolved_id, tokens, timeout=opts.timeout, cwd=opts.cwd)
    emit_result(result, as_json=opts.as_json)
    if result.parse_error:
        echo_stderr(f"parse error: {result.parse_error}")
    elif opts.as_json and result.parsed is None:
        echo_stderr("note: --json output is null (no parser: configured, or it returned None)")
    if not result.ok:
        echo_stderr(result.stderr)
        # Docs contract: exit 1 = tool ran and failed; 2 = bad usage / unknown id.
        raise typer.Exit(1)
    # A parse failure under --json (the caller asked for parsed output and got
    # none) is itself a failure.
    if opts.as_json and result.parsed is None and result.parse_error:
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


def run_tool_tokens(
    resolved_id: str, tokens: list[str], *, timeout: float | None = None, cwd: str | None = None
) -> ToolResult:
    """Build argv from CLI tokens and run the tool, mapping errors to exit codes."""
    try:
        tool = Tool(resolved_id)
        kwargs, extra = RunArgParser.parse(tokens, flag_names=flag_names_for(tool))
        return tool.run(extra=extra, timeout=timeout, cwd=cwd, **kwargs)
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
