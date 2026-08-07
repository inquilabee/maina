"""Kwargs → argv mapping from registry arg schema.

``type: path`` in YAML is emit-only metadata (help/docs). Values are still
passed through as strings like other non-flag types; there is no ``Path``
coercion at the ArgParser boundary.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from pyporter.core.errors import ArgError
from pyporter.core.registry import ArgSpec

if TYPE_CHECKING:
    from collections.abc import Mapping


class ArgParser:
    """Instance-scoped kwargs→argv builder for one args schema."""

    def __init__(self, schema: Mapping[str, ArgSpec | Mapping[str, Any]]) -> None:
        self.schema = {
            name: (
                spec
                if isinstance(spec, ArgSpec)
                else ArgSpec.from_mapping(name, dict(spec), tool_id="<schema>")
            )
            for name, spec in schema.items()
        }

    def build(
        self,
        cli: str,
        subcommands: list[str],
        kwargs: dict[str, Any],
        *,
        extra: str | list[str] | None = None,
    ) -> list[str]:
        """Build argv: cli + subcommand segments + mapped options + extra."""
        argv = [cli, *subcommands]
        if unknown := set(kwargs) - set(self.schema):
            names = ", ".join(sorted(unknown))
            raise ArgError(f"unknown arguments: {names}")

        positionals: list[str] = []
        options: list[str] = []
        for name, spec in self.schema.items():
            value = self._resolve_value(name, spec, kwargs)
            if value is None:
                continue
            tokens = self._emit_arg(name, spec, value)
            bucket = positionals if spec.positional else options
            bucket.extend(tokens)

        argv.extend(options)
        argv.extend(positionals)
        argv.extend(self.normalize_extra(extra))
        return argv

    @staticmethod
    def _resolve_value(name: str, spec: ArgSpec, kwargs: dict[str, Any]) -> Any:
        """Resolve one kwarg, applying defaults and required checks."""
        if name in kwargs:
            return kwargs[name]
        if spec.has_default:
            return spec.default
        if not spec.required:
            return None
        raise ArgError(f"missing required argument: {name}")

    @staticmethod
    def _emit_arg(name: str, spec: ArgSpec, value: Any) -> list[str]:
        """Emit argv tokens for one resolved argument."""
        if spec.type == "flag":
            return ArgParser._emit_flag(name, spec.flag, value)
        if isinstance(value, list):
            return ArgParser._emit_list(
                name,
                spec.flag,
                value,
                repeat=spec.repeat,
                join=spec.join,
                positional=spec.positional,
            )
        token = str(value)
        if spec.positional:
            return [token]
        if not spec.flag:
            raise ArgError(f"argument {name!r} needs a flag or positional: true")
        return [spec.flag, token]

    @staticmethod
    def _emit_flag(name: str, flag: str | None, value: Any) -> list[str]:
        """Emit a boolean CLI flag."""
        if not flag:
            raise ArgError(f"flag argument {name!r} requires flag:")
        if value is True:
            return [flag]
        if value is False or value is None:
            return []
        raise ArgError(f"flag argument {name!r} expects bool, got {type(value).__name__}")

    @staticmethod
    def _emit_list(
        name: str,
        flag: str | None,
        values: list[Any],
        *,
        repeat: Any,
        join: str | None,
        positional: Any,
    ) -> list[str]:
        """Emit list values as CSV, positionals, or repeated flags."""
        tokens = [str(v) for v in values]
        return (
            (
                ArgParser._emit_csv(name, flag, tokens, positional=positional)
                if join == "csv"
                else (
                    tokens
                    if positional or not flag
                    else ArgParser._emit_flag_list(flag, tokens, repeat=repeat)
                )
            )
            if tokens
            else []
        )

    @staticmethod
    def _emit_flag_list(flag: str, tokens: list[str], *, repeat: Any) -> list[str]:
        """Emit list values after a flag (repeated or single)."""
        if repeat is False:
            return [flag, *tokens]
        out: list[str] = []
        for token in tokens:
            out.extend([flag, token])
        return out

    @staticmethod
    def _emit_csv(
        name: str,
        flag: str | None,
        tokens: list[str],
        *,
        positional: Any,
    ) -> list[str]:
        """Join list values with commas for a single CLI token."""
        joined = ",".join(tokens)
        if positional:
            return [joined]
        if not flag:
            raise ArgError(f"argument {name!r} needs a flag for csv join")
        return [flag, joined]

    @staticmethod
    def normalize_extra(extra: str | list[str] | None) -> list[str]:
        """Normalize extra passthrough args to an argv suffix."""
        return (
            [] if extra is None else (shlex.split(extra) if isinstance(extra, str) else list(extra))
        )
