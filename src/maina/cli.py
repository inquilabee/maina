"""maina CLI: prepare, help, and run via Tool."""

from __future__ import annotations

import json
import shlex
import sys
import warnings
from contextlib import contextmanager
from typing import TYPE_CHECKING, Annotated, Any

import typer

from maina import catalog_validate
from maina.clitokens import (
    RAW_ARGV,
    RunOpts,
    is_empty,
    parse_run_tokens,
    split_at_dashdash,
    split_maina_opts,
)
from maina.clitokens import resolve as resolve_run_invocation
from maina.core.errors import (
    AbstractToolError,
    ArgError,
    InstallError,
    MainaError,
    ManifestError,
)
from maina.core.registry import RESERVED_RUN_PARAMS, build_manifest_schema
from maina.runtime import runtime
from maina.tool import Tool

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    # typer vendors its own click fork; `Context.parent` is declared against
    # that base class, not the `typer.Context` subclass, so a helper that
    # accepts either (see echo_typer_help) has to type against the base.
    from typer._click.core import Context as ClickContext

    from maina.core.result import ToolResult
    from maina.installer import Installer

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


def echo_typer_help(ctx: ClickContext) -> None:
    """Print Typer/Click help for ``ctx`` (no registry tool catalog)."""
    typer.echo(ctx.get_help())


@contextmanager
def rendered_warnings() -> Iterator[None]:
    """Catch a ``RuntimeWarning`` from the registry/installer (a skipped
    broken manifest, a same-root id collision, ...) and print it as a plain
    ``warning: ...`` line on stderr, instead of the raw
    ``.../loader.py:N: RuntimeWarning: ...`` Python default — a CLI user
    should never see maina's own source path and line number.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            yield
        finally:
            for one in caught:
                typer.echo(f"warning: {one.message}", err=True)


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
    with rendered_warnings():
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

    Checks that the manifest loads and its ``parser:`` resolves; that a concrete
    manifest has a ``cli:``; that ``install.backend`` is registered and (for
    ``github``) carries ``repo`` + ``assets`` (or ``asset_template``); that
    ``install.assets`` /
    ``sha256`` platform keys are real; that every ``output.from_file``
    placeholder names a declared arg; and that no two files under one root
    derive the same id (one would silently shadow the other). Prints
    ``ok:`` / ``error:`` per id and exits non-zero if any failed.
    """
    rt = runtime()
    duplicate_files, problems = catalog_validate.validate_ids(rt, tools)
    if not problems and not duplicate_files:
        typer.echo("no manifests registered")
        return
    failed = emit_duplicate_id_errors(duplicate_files)
    for tid, problem in problems.items():
        if problem is None:
            typer.echo(f"ok: {tid}")
        else:
            typer.echo(f"error: {tid}: {problem}", err=True)
            failed += 1
    if failed:
        typer.echo(f"{failed} manifest(s) failed", err=True)
        raise typer.Exit(1)


def emit_duplicate_id_errors(duplicate_files: dict[str, list[Path]]) -> int:
    """Print an ``error:`` line per same-root id collision; return how many."""
    count = 0
    for tid, paths in sorted(duplicate_files.items()):
        files = ", ".join(str(p) for p in paths)
        typer.echo(f"error: {tid}: derived from {len(paths)} manifests: {files}", err=True)
        count += 1
    return count


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
    reject_bad_prepare_flags(tools, check=check, force=force)
    installer = runtime().installer
    if check:
        with rendered_warnings():
            emit_check(installer, tools)
        return
    try:
        for cli in installer.clean_ids(tools) if force else []:
            typer.echo(f"cleaned: {cli}")
        with rendered_warnings():
            prepared, failures = installer.prepare_ids(tools)
    except (ManifestError, InstallError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    emit_prepare_outcome(prepared, failures)


def reject_bad_prepare_flags(tools: list[str] | None, *, check: bool, force: bool) -> None:
    if force and check:
        raise typer.BadParameter("--force cannot be combined with --check (which installs nothing)")
    if force and not tools:
        raise typer.BadParameter("--force needs explicit tool ids ('maina clean' wipes the cache)")


def emit_prepare_outcome(prepared: list[str], failures: dict[str, str]) -> None:
    for tid in prepared:
        typer.echo(f"prepared: {tid}")
    for tid, message in failures.items():
        typer.echo(f"failed: {tid}: {message}", err=True)
    if failures:
        raise typer.Exit(1)


def emit_check(installer: Installer, tools: list[str] | None) -> None:
    """``prepare --check``: print each id's state and exit non-zero if any
    missing or errored — one bad manifest never blanks the rest of the report.
    """
    satisfied, missing, failures = installer.check_ids(tools)
    for tid in satisfied:
        typer.echo(f"satisfied: {tid}")
    for tid in missing:
        typer.echo(f"missing: {tid}")
    for tid, message in failures.items():
        typer.echo(f"error: {tid}: {message}", err=True)
    if missing or failures:
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
    resolved_id, tokens, wants_help = resolve_run_invocation(tool_id, list(ctx.args))
    if resolved_id is None:
        echo_typer_help(ctx)
        raise typer.Exit(0)
    if wants_help or is_empty(tokens):
        emit_tool_help(resolved_id)
        raise typer.Exit(0)
    # Only the head (before `--`) is maina's; `--json` after `--` is the tool's.
    head, tail = split_at_dashdash(tokens)
    opts, head = split_maina_opts(head)
    tokens = head if tail is None else [*head, "--", *tail]
    dispatch_run(resolved_id, tokens, opts)


def dispatch_run(resolved_id: str, tokens: list[str], opts: RunOpts) -> None:
    """Print the argv (``--dry-run``) or run the tool and emit its result."""
    if opts.dry_run:
        typer.echo(shlex.join(dry_run_argv(resolved_id, tokens)))
        return
    result = run_tool_tokens(resolved_id, tokens, opts=opts)
    emit_result(result, as_json=opts.as_json)
    # The tool's stderr belongs to whoever ran it, on success too: a linter's
    # warnings and a deprecation notice are the reason it was written there.
    echo_stderr(result.stderr)
    if result.parse_error:
        echo_stderr(f"parse error: {result.parse_error}")
    elif opts.as_json and result.parsed is None:
        echo_stderr("note: --json output is null (no parser: configured, or it returned None)")
    if not result.ok:
        # Docs contract (docs/user/cli.md): 2 is a usage error (bad id /
        # manifest / args); everything else that stops the run, including a
        # tool that ran and failed, is 1.
        raise typer.Exit(1)
    # A parse failure under --json (the caller asked for parsed output and got
    # none) is itself a failure.
    if opts.as_json and result.parsed is None and result.parse_error:
        raise typer.Exit(1)


def dry_run_argv(resolved_id: str, tokens: list[str]) -> list[str]:
    """Build the argv a run would execute (argv[0] is the bare ``cli`` name)."""
    try:
        tool = Tool(resolved_id)
        kwargs, extra = parse_run_tokens(tokens, flag_names=flag_names_for(tool))
        return tool.argv(**reserved_safe_kwargs(kwargs, extra=extra))
    except (ManifestError, AbstractToolError, ArgError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def flag_names_for(tool: Tool) -> frozenset[str]:
    return frozenset(name for name, spec in tool.args.items() if spec.type == "flag")


def reserved_safe_kwargs(kwargs: dict[str, Any], **extra_params: Any) -> dict[str, Any]:
    """Merge parsed manifest kwargs with *extra_params* for a ``Tool.run`` /
    ``Tool.argv`` call, routing any manifest arg name that collides with one of
    ``Tool.run``'s own parameters (``parser``, ``timeout``, ``cwd``, ``env``,
    ``args``, …) through the ``args={...}`` escape hatch — otherwise it is
    silently absorbed by ``run`` or crashes it with a duplicate keyword.
    """
    reserved = {k: v for k, v in kwargs.items() if k in RESERVED_RUN_PARAMS}
    plain = {k: v for k, v in kwargs.items() if k not in RESERVED_RUN_PARAMS}
    return {**extra_params, **plain, **({"args": reserved} if reserved else {})}


def run_tool_tokens(resolved_id: str, tokens: list[str], *, opts: RunOpts) -> ToolResult:
    """Build argv from CLI tokens and run the tool, mapping errors to exit codes."""
    try:
        tool = Tool(resolved_id)
        kwargs, extra = parse_run_tokens(tokens, flag_names=flag_names_for(tool))
        call_kwargs = reserved_safe_kwargs(
            kwargs,
            extra=extra,
            timeout=opts.timeout,
            cwd=opts.cwd,
            stdin=sys.stdin.read() if opts.stdin else None,
            env_allow=opts.env_allow,
        )
        return tool.run(**call_kwargs)
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
        tool = Tool(tool_id)
    except ManifestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(tool.help())
    if not tool.manifest.abstract and all(not a.required for a in tool.args.values()):
        typer.echo(f"\n(no required arguments — run it with:  maina run {tool_id} --)")


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
