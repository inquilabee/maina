"""Public Tool API."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from maina.core.errors import AbstractToolError
from maina.core.registry import Registry
from maina.runner import ToolRunner
from maina.runtime import runtime as active_runtime

if TYPE_CHECKING:
    from collections.abc import Iterable

    from maina.core.registry import ArgSpec, Manifest
    from maina.core.result import ToolResult
    from maina.runtime import Runtime


class Tool:
    """Call non-Python CLIs via registry manifests.

    Usage:
      from maina import Tool
      result = Tool("jscpd").run(path=["src"])
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

    @property
    def id(self) -> str | None:
        return self._id

    def run(
        self,
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env_allow: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Run this tool surface with native kwargs.

        ``timeout`` caps the subprocess (seconds); ``env_allow`` trims the
        environment forwarded to the tool to essentials plus the named keys
        (see :meth:`maina.runner.ToolRunner.run`).
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
            kwargs,
            extra=extra,
            parser=parser,
            cwd=cwd,
            timeout=timeout,
            env_allow=env_allow,
        )

    def __call__(
        self,
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env_allow: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        return self.run(
            extra=extra,
            parser=parser,
            cwd=cwd,
            timeout=timeout,
            env_allow=env_allow,
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
            base = f"  {name}: {spec.type} {flag} ({req})"
            desc = spec.description or spec.help
            lines.append(f"{base} — {desc}" if desc else base)
        return lines
