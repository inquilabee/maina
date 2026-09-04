"""Kwargs → argv mapping from registry arg schema.

``type: path`` in YAML is emit-only metadata (help/docs). Values are still
passed through as strings like other non-flag types; there is no ``Path``
coercion at the ArgParser boundary.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from maina.core.errors import ArgError
from maina.core.registry import ArgSpec

if TYPE_CHECKING:
    from collections.abc import Mapping


class ArgParser:
    """Instance-scoped kwargs→argv builder for one args schema."""

    @staticmethod
    def _arg_error(name: str, detail: str) -> ArgError:
        """An ``ArgError`` for one named argument, with a consistent prefix."""
        return ArgError(f"argument {name!r} {detail}")

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
        separator: str | None = None,
    ) -> list[str]:
        """Build argv: cli + subcommand segments + args (ordered) + extra.

        Default order is all options then all positionals, each in declaration
        order. An arg with an explicit ``order: <int>`` sorts by that instead —
        the escape hatch for a CLI that wants an option *after* a positional
        (``find <path> -name '*.py'``).

        ``separator`` (the manifest's top-level ``separator:``, typically ``--``)
        is emitted once before the first positional in the final order, so a
        caller value beginning with ``-`` cannot be read as an option.
        """
        argv = [cli, *subcommands]
        if unknown := set(kwargs) - set(self.schema):
            names = ", ".join(sorted(unknown))
            raise ArgError(f"unknown arguments: {names}")

        emitted = self._emit_all(kwargs)
        sep_pending = separator is not None
        for _rank, _idx, is_positional, tokens in emitted:
            if is_positional and sep_pending:
                argv.append(separator)  # type: ignore[arg-type]
                sep_pending = False
            argv.extend(tokens)
        argv.extend(self.normalize_extra(extra))
        return argv

    def _emit_all(self, kwargs: dict[str, Any]) -> list[tuple[float, int, bool, list[str]]]:
        """``(rank, decl_index, is_positional, tokens)`` per set arg, sorted for argv."""
        rows: list[tuple[float, int, bool, list[str]]] = []
        for idx, (name, spec) in enumerate(self.schema.items()):
            value = self._resolve_value(name, spec, kwargs)
            if value is None:
                continue
            tokens = self._emit_arg(name, spec, value)
            if not tokens:
                continue
            default_rank = 100.0 if spec.positional else 0.0
            rank = default_rank if spec.order is None else float(spec.order)
            rows.append((rank, idx, spec.positional, tokens))
        rows.sort(key=lambda row: (row[0], row[1]))
        return rows

    def resolved_values(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Every arg's effective value (caller value or manifest ``default:``).

        Args with no value and no default are omitted. Used to interpolate
        ``output.from_file`` so a defaulted arg still resolves in the template.
        """
        out: dict[str, Any] = {}
        for name, spec in self.schema.items():
            value = self._resolve_value(name, spec, kwargs)
            if value is not None:
                out[name] = value
        return out

    @staticmethod
    def _resolve_value(name: str, spec: ArgSpec, kwargs: dict[str, Any]) -> Any:
        """Resolve one kwarg, applying defaults and the required check.

        ``required: true`` is not satisfied by ``None`` — whether that ``None``
        comes from ``run(x=None)`` or from ``default: null`` — it still raises.
        """
        fallback = spec.default if spec.has_default else None
        value = kwargs.get(name, fallback)
        if value is None and spec.required:
            raise ArgError(f"missing required argument: {name}")
        return value

    @staticmethod
    def _emit_arg(name: str, spec: ArgSpec, value: Any) -> list[str]:
        """Emit argv tokens for one resolved argument."""
        if spec.type == "flag":
            return ArgParser._emit_flag(name, spec.flag, value)
        if spec.type == "pairs":
            return ArgParser._emit_pairs(name, spec.flag, value, joiner=spec.joiner)
        if isinstance(value, list):
            return ArgParser._emit_list(
                name,
                spec.flag,
                value,
                repeat=spec.repeat,
                join=spec.join,
                positional=spec.positional,
                joiner=spec.joiner,
            )
        token = str(value)
        if spec.positional:
            return [token]
        if not spec.flag:
            raise ArgParser._arg_error(name, "needs a flag or positional: true")
        return (
            [f"{spec.flag}{spec.joiner}{token}"] if spec.joiner is not None else [spec.flag, token]
        )

    @staticmethod
    def _emit_pairs(
        name: str, flag: str | None, value: Any, *, joiner: str | None = None
    ) -> list[str]:
        """Emit one flag per (key, value) pair.

        Default is three tokens ``--flag key value`` (``jq --arg name val``).
        With ``joiner:`` set it is two — ``--flag key<joiner>value`` — for
        ``docker -e K=V`` (``joiner: "="``) and the like.
        """
        if not flag:
            raise ArgParser._arg_error(name, "type: pairs needs a flag")
        items = value.items() if isinstance(value, dict) else value
        out: list[str] = []
        for pair in items:
            if isinstance(pair, (str, bytes)) or not isinstance(pair, (tuple, list)):
                raise ArgParser._arg_error(
                    name, "pairs expects a dict or a list of [key, value] pairs"
                )
            try:
                key, val = pair
            except ValueError as exc:
                raise ArgParser._arg_error(name, "each pair must be exactly [key, value]") from exc
            if joiner is None:
                out.extend([flag, str(key), str(val)])
            else:
                out.extend([flag, f"{key}{joiner}{val}"])
        return out

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
        joiner: str | None = None,
    ) -> list[str]:
        """Emit list values as CSV, positionals, or repeated flags."""
        tokens = [str(v) for v in values]
        return (
            (
                ArgParser._emit_csv(name, flag, tokens, positional=positional, joiner=joiner)
                if join == "csv"
                else (
                    tokens
                    if positional or not flag
                    else ArgParser._emit_flag_list(flag, tokens, repeat=repeat, joiner=joiner)
                )
            )
            if tokens
            else []
        )

    @staticmethod
    def _emit_flag_list(
        flag: str, tokens: list[str], *, repeat: Any, joiner: str | None = None
    ) -> list[str]:
        """Emit list values after a flag (repeated or single)."""
        if joiner is not None:
            return [f"{flag}{joiner}{token}" for token in tokens]
        repeated = [item for token in tokens for item in (flag, token)]
        return [flag, *tokens] if repeat is False else repeated

    @staticmethod
    def _emit_csv(
        name: str,
        flag: str | None,
        tokens: list[str],
        *,
        positional: Any,
        joiner: str | None = None,
    ) -> list[str]:
        """Join list values with commas for a single CLI token."""
        joined = ",".join(tokens)
        if positional:
            return [joined]
        if not flag:
            raise ArgParser._arg_error(name, "needs a flag for csv join")
        return [f"{flag}{joiner}{joined}"] if joiner is not None else [flag, joined]

    @staticmethod
    def normalize_extra(extra: str | list[str] | None) -> list[str]:
        """Normalize extra passthrough args to an argv suffix."""
        return (
            [] if extra is None else (shlex.split(extra) if isinstance(extra, str) else list(extra))
        )
