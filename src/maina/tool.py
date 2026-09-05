"""Public Tool API."""

from __future__ import annotations

import inspect
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from maina.core.argparser import ArgParser
from maina.core.errors import AbstractToolError, ManifestError
from maina.core.registry import Registry
from maina.runner import ToolRunner
from maina.runtime import register as _compose_register
from maina.runtime import runtime as active_runtime

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from pathlib import Path

    from maina.core.parsers import ParserInput
    from maina.core.protocols import InstallerBackend, LanguageRuntimeProvider
    from maina.core.registry import ArgSpec, Manifest
    from maina.core.result import ToolResult
    from maina.runtime import Runtime


class Tool:
    """Call a CLI described by a registry manifest.

    Usage:
      import maina
      app = maina.register(maina.Runtime.preferred(), registry_dirs=["my_tools"])
      result = app.tool("mytool").run(path=["src"])
      print(result.raw, result.parsed)

    For the ``maina`` CLI, run ``maina --help``.
    """

    def __init__(
        self,
        name: str | None = None,
        *,
        subcommand: str | None = None,
        runtime: Runtime | None = None,
    ) -> None:
        self._runtime = runtime if runtime is not None else active_runtime()
        self._registry = self._runtime.registry
        self._id = Registry.join_tool_id(name, subcommand) if name else None
        self._manifest: Manifest | None = (
            None if self._id is None else self._registry.load(self._id)
        )
        if self._manifest is not None:
            self._check_parser(self._manifest)

    def _check_parser(self, manifest: Manifest) -> None:
        """Fail at construction on a ``parser:`` this runtime cannot resolve —
        not after a subprocess has already run (an unknown *.py* module is
        already caught at load; this is the general-name case)."""
        ref = manifest.parser
        if (
            ref is not None
            and manifest.parser_path is None
            and not self._runtime.parsers.is_general(ref)
        ):
            raise ManifestError(
                f"manifest {manifest.id!r} parser {ref!r} is not a "
                "registered parser or a *.py module"
            )

    @property
    def id(self) -> str | None:
        return self._id

    @property
    def manifest(self) -> Manifest:
        """The resolved manifest for this bound tool."""
        if self._manifest is None:
            raise AbstractToolError("unbound Tool() has no manifest")
        return self._manifest

    @property
    def args(self) -> Mapping[str, ArgSpec]:
        """Read-only view of this tool's argument schema."""
        return MappingProxyType(self.manifest.args)

    def describe(self) -> dict[str, Any]:
        """A JSON-safe, stable description — build your own help / form / CLI."""
        m = self.manifest
        return {
            "id": m.id,
            "cli": m.data.get("cli"),
            "subcommands": list(m.subcommands),
            "version": m.version,
            "abstract": m.abstract,
            "parser": m.parser,
            "separator": m.separator,
            "platforms": list(m.platforms),
            "timeout": m.data.get("timeout"),
            "requires": dict(m.requires.runtimes),
            "env": dict(m.env),
            "args": {name: self._describe_arg(spec) for name, spec in m.args.items()},
            "install": {
                "backend": m.install.backend,
                "repo": m.install.repo,
                "assets": dict(m.install.assets),
                "binary": m.install.binary,
                "tag": m.install.tag,
                "package": m.install.package,
                "entry": m.install.entry,
                "sha256": dict(m.install.sha256),
                "allow_scripts": m.install.allow_scripts,
                "allow_any_executable": m.install.allow_any_executable,
            },
            "output": {
                "format": m.output.format,
                "success_codes": list(m.output.success_codes),
                "from_file": m.output.from_file,
            },
        }

    @staticmethod
    def _describe_arg(spec: ArgSpec) -> dict[str, Any]:
        return {
            "type": spec.type,
            "flag": spec.flag,
            "joiner": spec.joiner,
            "positional": spec.positional,
            "required": spec.required,
            "repeat": spec.repeat,
            "join": spec.join,
            "order": spec.order,
            "default": spec.default if spec.has_default else None,
            "has_default": spec.has_default,
            "description": spec.description or spec.help,
            "choices": list(spec.choices) if spec.choices else None,
        }

    def run(
        self,
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env_allow: Iterable[str] | None = None,
        env: dict[str, str] | None = None,
        stdin: str | bytes | None = None,
        text: bool = True,
        args: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Run this tool surface with native kwargs.

        ``timeout`` caps the subprocess (seconds); ``env_allow`` trims the
        environment forwarded to the tool to essentials plus the named keys
        (see :meth:`maina.runner.ToolRunner.run`); ``env={...}`` overlays extra
        variables for this call; ``stdin`` is written to the tool's stdin;
        ``text=False`` returns ``bytes`` in ``result.raw_bytes`` (``result.raw``
        stays a lossy-decoded text view) and skips the parser (for a tool that
        emits binary on stdout). Pass a manifest arg whose name
        collides with one of these parameters (``parser``, ``timeout``, ``cwd``
        …) via ``args={...}``.
        """
        if self._manifest is None:
            raise AbstractToolError("Tool() with no name cannot run; use help() or pass a name")
        if self._manifest.abstract:
            raise AbstractToolError(
                f"{self._manifest.id!r} is abstract; call a concrete child surface"
            )
        return ToolRunner(
            self._manifest,
            installer=self._runtime.installer,
            parsers=self._runtime.parsers,
        ).run(
            {**kwargs, **(dict(args) if args else {})},
            extra=extra,
            parser=parser,
            cwd=cwd,
            timeout=timeout,
            env_allow=env_allow,
            env=env,
            stdin=stdin,
            text=text,
        )

    def argv(
        self,
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env_allow: Iterable[str] | None = None,
        env: dict[str, str] | None = None,
        stdin: str | bytes | None = None,
        text: bool = True,
        args: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """The argv this tool would run, with the bare ``cli`` name as argv[0].

        A real :meth:`run` resolves argv[0] to the installed/cached executable;
        every token after it is exactly what ``run`` would pass. Use it for a
        dry run, a preview, or to drive the subprocess yourself.

        Binds the same run-only parameters ``run()`` does (``parser``, ``cwd``,
        ``timeout``, ``env_allow``, ``env``, ``stdin``, ``text``) — accepted for
        symmetry and ignored here, since none affects argv. A manifest arg whose
        name collides with one of them is reached the same way ``run()`` reaches
        it: ``argv(args={"timeout": ...})``, never a same-named plain kwarg.
        """
        del parser, cwd, timeout, env_allow, env, stdin, text
        manifest = self.manifest
        if manifest.abstract:
            raise AbstractToolError(f"{manifest.id!r} is abstract; call a concrete child surface")
        return ArgParser(manifest.args, tool_id=manifest.id).build(
            manifest.cli,
            manifest.subcommands,
            {**kwargs, **(dict(args) if args else {})},
            extra=extra,
            separator=manifest.separator,
        )

    def __call__(
        self,
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env_allow: Iterable[str] | None = None,
        env: dict[str, str] | None = None,
        stdin: str | bytes | None = None,
        text: bool = True,
        args: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        return self.run(
            extra=extra,
            parser=parser,
            cwd=cwd,
            timeout=timeout,
            env_allow=env_allow,
            env=env,
            stdin=stdin,
            text=text,
            args=args,
            **kwargs,
        )

    def commands(self) -> list[str]:
        """List concrete child surfaces (or all concrete ids if unbound)."""
        return self._registry.list_commands(self._id)

    def __str__(self) -> str:
        """Format library or bound-tool help text (not the CLI shell)."""
        if self._manifest is None:
            body = inspect.cleandoc(type(self).__doc__ or "")
            tops = self._registry.list_commands(None)
            return "\n".join([body, "", "Registered tools:", *self.format_tool_id_lines(tops)])
        manifest = self._manifest
        lines = [
            f"{self.__class__.__name__}: {manifest.id}",
            f"cli: {manifest.data.get('cli')}",
        ]
        if manifest.abstract:
            lines.append("abstract: true")
        lines.extend(
            [
                f"subcommands: {' '.join(manifest.subcommands) or '(none)'}",
                f"version: {manifest.version or '(unpinned)'}",
                "",
                "Arguments:",
                *self._arg_help_lines(manifest.args),
            ]
        )
        if children := self._registry.list_commands(manifest.id):
            lines.extend(["", "Child surfaces:", *self.format_tool_id_lines(children)])
        return "\n".join(lines)

    def help(self) -> str:
        """Format library or bound-tool help (does not print)."""
        return str(self)

    @staticmethod
    def format_tool_id_lines(ids: list[str]) -> list[str]:
        """Format a bullet list of tool ids for help text."""
        return [f"  - {t}" for t in ids] if ids else ["  (none registered)"]

    @staticmethod
    def _arg_help_lines(args: dict[str, ArgSpec]) -> list[str]:
        """Format argument help lines for a manifest args map."""
        if not args:
            return ["  (none declared; use extra=...)"]
        lines: list[str] = []
        for name, spec in args.items():
            flag = spec.flag or ("positional" if spec.positional else "")
            req = "required" if spec.required else "optional"
            default = f", default: {spec.default!r}" if spec.has_default else ""
            choices = f", choices: {{{', '.join(spec.choices)}}}" if spec.choices else ""
            base = f"  {name}: {spec.type} {flag} ({req}{default}{choices})"
            desc = spec.description or spec.help
            lines.append(f"{base} — {desc}" if desc else base)
        return lines


class MainaApp:
    """One ``Runtime``'s registered catalog, bound to a ``Tool()`` factory.

    Returned by :func:`register` so a caller doesn't thread the same
    ``Runtime`` through every ``Tool(..., runtime=rt)`` call by hand, and so
    several independent apps (separate registry/parsers dirs, separate cache)
    can coexist in one process without going through the process-wide
    ``SESSION`` singleton (:class:`~maina.runtime.RuntimeSession`).
    """

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def tool(self, name: str | None = None, *, subcommand: str | None = None) -> Tool:
        """``Tool(name, runtime=self.runtime)`` — bound to this app's catalog."""
        return Tool(name, subcommand=subcommand, runtime=self.runtime)

    def __repr__(self) -> str:
        return f"MainaApp({self.runtime!r})"


def register(
    rt: Runtime | None = None,
    /,
    *,
    registry_dirs: Path | str | Sequence[Path | str] | None = None,
    parsers: Mapping[str, ParserInput] | None = None,
    parsers_dirs: Path | str | Sequence[Path | str] | None = None,
    installers: Sequence[InstallerBackend] | None = None,
    language: LanguageRuntimeProvider | None = None,
    replace: bool = False,
    runtime: Runtime | None = None,
) -> MainaApp:
    """Mutate a Runtime (positional or ``runtime=``) or the active session; return a MainaApp.

    Prefer ``register(rt, ...)``. ``runtime=`` is an alternate keyword spelling of the
    same Runtime. Passing both raises ``ValueError``. With neither, mutates
    ``maina.runtime()`` (the process-wide active session).

    The returned :class:`MainaApp` wraps the mutated Runtime — call ``.tool(name)``
    on it instead of threading the Runtime through every ``Tool(..., runtime=...)``
    call, and to run several independent catalogs in one process without touching
    the shared session::

        app = maina.register(maina.Runtime.preferred(), registry_dirs=["my_tools"])
        result = app.tool("mytool").run(path=["src"])
    """
    target = _compose_register(
        rt,
        registry_dirs=registry_dirs,
        parsers=parsers,
        parsers_dirs=parsers_dirs,
        installers=installers,
        language=language,
        replace=replace,
        runtime=runtime,
    )
    return MainaApp(target)
