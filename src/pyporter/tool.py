"""Public Tool API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyporter.errors import AbstractToolError
from pyporter.registry import Registry
from pyporter.runner import ToolRunner
from pyporter.runtime import runtime as active_runtime

if TYPE_CHECKING:
    from pyporter.registry import Manifest
    from pyporter.result import ToolResult
    from pyporter.runtime import Runtime


class Tool:
    """Registry-backed CLI surface composed from Runtime + ToolRunner."""

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
        **kwargs: Any,
    ) -> ToolResult:
        """Run this tool surface with native kwargs."""
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
        )

    def __call__(
        self,
        *,
        extra: str | list[str] | None = None,
        parser: Any = None,
        cwd: str | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        return self.run(extra=extra, parser=parser, cwd=cwd, **kwargs)

    def commands(self) -> list[str]:
        """List child registry surfaces for this tool (or top-level if unbound)."""
        return self._registry.list_commands(self._id)

    def __str__(self) -> str:
        """Format package or bound-tool help text."""
        if self._manifest is None:
            return self.package_help()
        manifest = self._manifest
        lines = [
            f"Tool: {manifest.id}",
            f"cli: {manifest.data.get('cli')}",
            f"abstract: {manifest.abstract}",
            f"subcommands: {' '.join(manifest.subcommands) or '(none)'}",
            f"version: {manifest.version or '(unpinned)'}",
            "",
            "Arguments:",
            *self._arg_help_lines(manifest.args),
        ]
        if children := self._registry.list_commands(manifest.id):
            lines.extend(["", "Child surfaces:", *[f"  - {c}" for c in children]])
        return "\n".join(lines)

    def help(self) -> str:
        """Emit package or bound-tool usage help."""
        text = str(self)
        print(text)
        return text

    @classmethod
    def package_help(cls) -> str:
        """Format unbound package usage help."""
        tops = active_runtime().registry.list_commands(None)
        lines = [
            "pyporter — call non-Python CLIs via registry manifests",
            "",
            "Usage:",
            "  from pyporter import Tool",
            '  result = Tool("jscpd").run(path=["src"])',
            "  print(result.raw, result.parsed)",
            "",
            "CLI:",
            "  pyporter prepare [tool...]",
            "  pyporter run <tool_id> [key=value ...] [-- extra...]",
            "  pyporter run <tool_id> --help",
            "  pyporter help",
            "",
            "Top-level tools:",
            *([f"  - {t}" for t in tops] if tops else ["  (none registered)"]),
        ]
        return "\n".join(lines)

    @classmethod
    def run_usage_help(cls) -> str:
        """Format help for ``pyporter run`` without a tool id."""
        tops = active_runtime().registry.list_commands(None)
        lines = [
            "Usage:",
            "  pyporter run <tool_id> [key=value ...] [-- extra...]",
            "  pyporter run <tool_id> --help",
            "",
            "Pass registry kwargs as key=value tokens. Repeat a key for list values.",
            "A bare key sets that flag to true. Tokens after -- become extra.",
            "",
            "See also: pyporter help",
            "",
            "Top-level tools:",
            *([f"  - {t}" for t in tops] if tops else ["  (none registered)"]),
        ]
        return "\n".join(lines)

    @staticmethod
    def _arg_help_lines(args: dict[str, Any]) -> list[str]:
        """Format argument help lines for a manifest args map."""
        if not args:
            return ["  (none declared; use extra=...)"]
        lines: list[str] = []
        for name, spec in args.items():
            flag = spec.get("flag") or ("positional" if spec.get("positional") else "")
            typ = spec.get("type", "string")
            req = "required" if spec.get("required") else "optional"
            base = f"  {name}: {typ} {flag} ({req})"
            desc = spec.get("description") or spec.get("help")
            lines.append(f"{base} — {desc}" if desc else base)
        return lines
