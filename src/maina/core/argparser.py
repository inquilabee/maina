"""Kwargs → argv mapping from registry arg schema.

``type: path`` in YAML is emit-only metadata (help/docs) — it does not change
how a value is emitted. An ``os.PathLike`` value is accepted for any arg and
converted with ``os.fspath``, since a path is the most common thing a wrapped
CLI takes and ``pathlib.Path`` is what Python callers already hold.
"""

from __future__ import annotations

import os
import shlex
from typing import TYPE_CHECKING, Any

from maina.core.errors import ArgError
from maina.core.registry import ArgSpec

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


class ArgParser:
    """Instance-scoped kwargs→argv builder for one args schema."""

    @staticmethod
    def _arg_error(name: str, detail: str) -> ArgError:
        """An ``ArgError`` for one named argument, with a consistent prefix."""
        return ArgError(f"argument {name!r} {detail}")

    def __init__(
        self, schema: Mapping[str, ArgSpec | Mapping[str, Any]], *, tool_id: str = "<tool>"
    ) -> None:
        self.tool_id = tool_id
        self.schema = {
            name: (
                spec
                if isinstance(spec, ArgSpec)
                else ArgSpec.from_mapping(name, dict(spec), tool_id=tool_id)
            )
            for name, spec in schema.items()
        }

    def _known_args(self) -> str:
        """``'a, b, c'`` of declared arg names, or ``'(none)'`` — for error text."""
        return ", ".join(self.schema) or "(none)"

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

        ``extra`` passthrough tokens land just *before* that separator (they are
        caller-supplied options, not operands); with no separator, or none
        emitted, they go at the end as before.
        """
        argv = [cli, *subcommands]
        if unknown := set(kwargs) - set(self.schema):
            names = ", ".join(sorted(unknown))
            raise ArgError(
                f"{self.tool_id}: unknown arguments: {names} (known: {self._known_args()})"
            )

        emitted = self._emit_all(kwargs)
        extra_tokens = self.normalize_extra(extra)
        sep_pending = separator is not None
        for _rank, _idx, is_positional, tokens in emitted:
            if is_positional and sep_pending:
                argv.extend(extra_tokens)
                argv.append(separator)  # type: ignore[arg-type]
                sep_pending = False
            argv.extend(tokens)
        if sep_pending or separator is None:
            argv.extend(extra_tokens)
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

    def _resolve_value(self, name: str, spec: ArgSpec, kwargs: dict[str, Any]) -> Any:
        """Resolve one kwarg, applying defaults and the required check.

        ``required: true`` is not satisfied by ``None`` — whether that ``None``
        comes from ``run(x=None)`` or from ``default: null`` — it still raises.
        """
        fallback = spec.default if spec.has_default else None
        value = kwargs.get(name, fallback)
        if value is None and spec.required:
            raise ArgError(
                f"{self.tool_id}: missing required argument: {name} (known: {self._known_args()})"
            )
        if value is not None and spec.choices is not None:
            self._check_choices(name, spec.choices, value)
        return value

    def _check_choices(self, name: str, choices: tuple[str, ...], value: Any) -> None:
        """Every value (or, for a list, every item) must be one of *choices*."""
        items = value if isinstance(value, list) else [value]
        if bad := sorted({str(v) for v in items if str(v) not in choices}):
            allowed = ", ".join(choices)
            raise ArgError(
                f"{self.tool_id}: argument {name!r} must be one of {{{allowed}}}, "
                f"got {', '.join(bad)}"
            )

    @staticmethod
    def _scalar_token(name: str, value: Any, *, what: str = "value") -> str:
        """Stringify one argv-bound value, rejecting ``bool`` and non-scalars.

        ``bool`` is a Python ``int`` subclass, so an unguarded ``str()`` turns a
        caller's ``False`` into the literal token ``"False"``; a dict/list/other
        object turns into its ``repr()``. Neither is a value a downstream CLI can
        use — a caller who wants a boolean flag wants ``type: flag``.

        Every value that reaches argv goes through here, ``type: pairs`` keys and
        values included (*what* names which half for the error message).

        ``os.PathLike`` is accepted and converted: a path is the most common
        thing a wrapped CLI takes, and ``pathlib.Path`` is what Python callers
        already hold. ``os.fspath`` is unambiguous, unlike ``str()`` on a bool.
        """
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ArgParser._arg_error(
                name, f"{what} must be a string or number, not {type(value).__name__}"
            )
        return str(value)

    @staticmethod
    def _emit_arg(name: str, spec: ArgSpec, value: Any) -> list[str]:
        """Emit argv tokens for one resolved argument."""
        if spec.type == "flag":
            return ArgParser._emit_flag(name, spec.flag, value, false_flag=spec.false_flag)
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
        token = ArgParser._scalar_token(name, value)
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

        Both halves of a pair are scalar-checked like every other argv token:
        ``env={"DEBUG": False}`` must not emit ``-e DEBUG=False``, which most
        CLIs read as *set and truthy*.
        """
        if not flag:
            raise ArgParser._arg_error(name, "type: pairs needs a flag")
        items = value.items() if isinstance(value, dict) else value
        out: list[str] = []
        for pair in items:
            key, val = ArgParser._pair_parts(name, pair)
            key_token = ArgParser._scalar_token(name, key, what="pairs key")
            val_token = ArgParser._scalar_token(name, val, what="pairs value")
            if joiner is None:
                out.extend([flag, key_token, val_token])
            else:
                out.extend([flag, f"{key_token}{joiner}{val_token}"])
        return out

    @staticmethod
    def _pair_parts(name: str, pair: Any) -> tuple[Any, Any]:
        """Unpack one ``type: pairs`` entry into ``(key, value)``."""
        if isinstance(pair, (str, bytes)) or not isinstance(pair, (tuple, list)):
            raise ArgParser._arg_error(name, "pairs expects a dict or a list of [key, value] pairs")
        try:
            key, val = pair
        except ValueError as exc:
            raise ArgParser._arg_error(name, "each pair must be exactly [key, value]") from exc
        return key, val

    @staticmethod
    def _emit_flag(
        name: str, flag: str | None, value: Any, *, false_flag: str | None = None
    ) -> list[str]:
        """Emit a boolean CLI flag.

        With ``false_flag:`` set, an explicit ``False`` emits that token instead
        of nothing — for a CLI whose off switch is its own flag (``--no-fix``),
        where "not passed" and "passed off" are different requests.
        """
        if not flag:
            raise ArgError(f"flag argument {name!r} requires flag:")
        if value is True:
            return [flag]
        if value is False:
            return [false_flag] if false_flag else []
        if value is None:
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
        tokens = [ArgParser._scalar_token(name, v) for v in values]
        return (
            (
                ArgParser._emit_joined(
                    name, flag, tokens, positional=positional, joiner=joiner, separator=join
                )
                if join is not None
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
    def _emit_joined(
        name: str,
        flag: str | None,
        tokens: list[str],
        *,
        positional: Any,
        joiner: str | None = None,
        separator: str = ",",
    ) -> list[str]:
        """Join list values with ``separator`` into a single CLI token."""
        joined = separator.join(tokens)
        if positional:
            return [joined]
        if not flag:
            raise ArgParser._arg_error(name, "needs a flag for a joined list")
        return [f"{flag}{joiner}{joined}"] if joiner is not None else [flag, joined]

    @staticmethod
    def normalize_extra(
        extra: str | os.PathLike[str] | Iterable[str | os.PathLike[str]] | None,
    ) -> list[str]:
        """Normalize extra passthrough args to an argv suffix."""
        if extra is None:
            return []
        if isinstance(extra, os.PathLike):
            return ArgParser._extra_tokens([extra])
        if not isinstance(extra, str):
            return ArgParser._extra_tokens(extra)
        try:
            return shlex.split(extra)
        except ValueError as exc:
            raise ArgError(f"extra={extra!r} is not a valid shell-quoted string: {exc}") from exc

    @staticmethod
    def _extra_tokens(extra: Iterable[Any]) -> list[str]:
        """``extra=[...]`` tokens: strings or paths only (a number is a call-site mistake)."""
        try:
            tokens = list(extra)
        except TypeError as exc:
            raise ArgError(f"extra must be a string or a list of strings, not {extra!r}") from exc
        if bad := [t for t in tokens if not isinstance(t, (str, os.PathLike))]:
            raise ArgError(f"extra tokens must be strings or paths, got {bad[0]!r}")
        return [os.fspath(t) for t in tokens]
