"""Registry domain types: args, install, requires, output, and Manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .common import (
    manifest_error,
    optional_str,
    require_bool,
    require_optional_bool,
    safe_segment,
    scalar_str,
    unknown_keys,
)
from .install import InstallSpec
from .keys import ARG_KEYS, ARG_TYPES, JOIN_WORD_HINTS, OUTPUT_KEYS
from .requires import RequiresSpec

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True, frozen=True)
class ArgSpec:
    """One registry arg entry (kwargs → argv schema).

    ``type: path`` is emit-only metadata for help/docs; values are still
    stringified like other non-flag types (no ``Path`` coercion).
    """

    type: str = "string"
    flag: str | None = None
    false_flag: str | None = None
    joiner: str | None = None
    positional: bool = False
    required: bool = False
    repeat: bool | None = None
    join: str | None = None
    order: int | None = None
    default: Any = field(default=None, hash=False, compare=False)
    has_default: bool = False
    description: str | None = None
    help: str | None = None
    choices: tuple[str, ...] | None = None

    @classmethod
    def from_mapping(cls, name: str, raw: Any, *, tool_id: str) -> ArgSpec:
        """Parse one arg map; fail closed on non-maps, unknown keys, and bad shapes."""
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, f"args.{name} must be a map")
        if bad := unknown_keys(raw, ARG_KEYS):
            raise manifest_error(tool_id, f"args.{name} unknown keys: {', '.join(bad)}")
        arg_type = str(raw.get("type", "string"))
        positional = require_bool(
            raw.get("positional"), field_name=f"args.{name}.positional", tool_id=tool_id
        )
        flag = cls._opt_scalar(raw, "flag", name=name, tool_id=tool_id)
        join = cls._opt_scalar(raw, "join", name=name, tool_id=tool_id)
        joiner = cls._opt_scalar(raw, "joiner", name=name, tool_id=tool_id)
        false_flag = cls._opt_scalar(raw, "false_flag", name=name, tool_id=tool_id)
        repeat = require_optional_bool(
            raw.get("repeat"), field_name=f"args.{name}.repeat", tool_id=tool_id
        )
        cls._check_shape(
            name,
            arg_type,
            positional=positional,
            flag=flag,
            false_flag=false_flag,
            join=join,
            joiner=joiner,
            repeat=repeat,
            tool_id=tool_id,
        )
        default = cls._scalar_default(raw, arg_type, name=name, tool_id=tool_id)
        choices = cls._parse_choices(raw, arg_type, name=name, tool_id=tool_id)
        if has_default := "default" in raw:
            cls._check_default_shape(
                default, arg_type=arg_type, choices=choices, name=name, tool_id=tool_id
            )
        return cls(
            type=arg_type,
            flag=flag,
            false_flag=false_flag,
            joiner=joiner,
            positional=positional,
            required=require_bool(
                raw.get("required"), field_name=f"args.{name}.required", tool_id=tool_id
            ),
            repeat=repeat,
            join=join,
            order=cls._parse_order(raw.get("order"), name=name, tool_id=tool_id),
            default=default,
            has_default=has_default,
            description=optional_str(raw.get("description")),
            help=optional_str(raw.get("help")),
            choices=choices,
        )

    @staticmethod
    def _check_default_shape(
        default: Any, *, arg_type: str, choices: tuple[str, ...] | None, name: str, tool_id: str
    ) -> None:
        """A ``default:`` must be usable — never true by construction, unlike
        ``required``: nothing else forces the caller to pass a value that
        would override it, so a manifest with a bad default loads clean and
        then fails every call that omits the argument."""
        if arg_type == "pairs" and default is not None and not isinstance(default, (dict, list)):
            raise manifest_error(
                tool_id,
                f"args.{name} default must be a mapping or a list of [key, value] pairs "
                f"for type: pairs, not {type(default).__name__}",
            )
        if choices is not None and default is not None and str(default) not in choices:
            allowed = ", ".join(choices)
            raise manifest_error(
                tool_id,
                f"args.{name} default {default!r} is not one of its own choices: {{{allowed}}}",
            )

    @staticmethod
    def _parse_choices(
        raw: dict[str, Any], arg_type: str, *, name: str, tool_id: str
    ) -> tuple[str, ...] | None:
        """``choices:`` — a non-empty list of scalar values the arg is limited to."""
        choices = raw.get("choices")
        if choices is None:
            return None
        if arg_type in ("flag", "pairs"):
            raise manifest_error(tool_id, f"args.{name} choices doesn't apply to type: {arg_type}")
        if not isinstance(choices, list) or not choices:
            raise manifest_error(tool_id, f"args.{name} choices must be a non-empty list")
        return tuple(
            scalar_str(v, field_name=f"args.{name}.choices[{i}]", tool_id=tool_id)
            for i, v in enumerate(choices)
        )

    @staticmethod
    def _opt_scalar(raw: dict[str, Any], key: str, *, name: str, tool_id: str) -> str | None:
        """An optional string arg field, run through the YAML-scalar guard."""
        return (
            None
            if raw.get(key) is None
            else scalar_str(raw[key], field_name=f"args.{name}.{key}", tool_id=tool_id)
        )

    @staticmethod
    def _scalar_default(raw: dict[str, Any], arg_type: str, *, name: str, tool_id: str) -> Any:
        """``default:`` — a ``flag`` keeps its bool; every other type rejects a
        bare YAML ``bool`` / ``float`` (``default: no`` must not become argv
        ``"False"``). Lists / dicts / ints pass through unchanged."""
        if "default" not in raw:
            return None
        value = raw["default"]
        keep_as_is = arg_type == "flag" or not isinstance(value, (bool, float))
        return (
            value
            if keep_as_is
            else scalar_str(value, field_name=f"args.{name}.default", tool_id=tool_id)
        )

    @staticmethod
    def _parse_order(order: Any, *, name: str, tool_id: str) -> int | None:
        if order is not None and (isinstance(order, bool) or not isinstance(order, int)):
            raise manifest_error(tool_id, f"args.{name} order {order!r} must be an integer")
        return order

    @staticmethod
    def _check_emit_conflicts(
        name: str,
        arg_type: str,
        *,
        positional: bool,
        join: str | None,
        joiner: str | None,
        repeat: bool | None,
        tool_id: str,
    ) -> None:
        """Reject field pairs argv can only half-honour.

        Each of these loads clean today and then silently picks a winner:
        ``join`` beats ``repeat``, ``joiner`` beats ``repeat: false`` (the
        one that bites — the author wrote "one flag, then every item" and gets a
        joined flag *per* item), and a ``flag`` / positional arg drops ``joiner``
        on the floor. An author who wrote both meant one of them.
        """
        if join is not None and repeat is not None:
            raise manifest_error(
                tool_id,
                f"args.{name} join and repeat are two different list layouts — join puts "
                "every item in one token, repeat emits the flag per item; pick one",
            )
        if joiner is not None and repeat is False:
            raise manifest_error(
                tool_id,
                f"args.{name} joiner and repeat: false conflict — a joiner emits "
                "flag<joiner>item once per item, never a single flag; drop one",
            )
        if joiner is not None and (positional or arg_type == "flag"):
            what = "positional" if positional else "type: flag"
            raise manifest_error(tool_id, f"args.{name} joiner is never emitted for a {what} arg")

    @staticmethod
    def _check_join(name: str, join: str | None, *, tool_id: str) -> None:
        """``join:`` is the separator itself, not a name for one."""
        if join is None:
            return
        if not join:
            raise manifest_error(
                tool_id, f'args.{name} join must be a non-empty separator, e.g. join: ","'
            )
        if (literal := JOIN_WORD_HINTS.get(join)) is not None:
            # A word here would join the list with that literal text (`a comma b`).
            raise manifest_error(
                tool_id,
                f"args.{name} join: {join} names a separator; write the separator itself: "
                f"join: {literal!r}",
            )

    @staticmethod
    def _check_false_flag(
        name: str, arg_type: str, *, false_flag: str | None, tool_id: str
    ) -> None:
        """``false_flag`` is the token emitted when a flag is explicitly off.

        Only a ``type: flag`` arg has an off state to express; on anything else
        it would silently never be emitted.
        """
        if false_flag is not None and arg_type != "flag":
            raise manifest_error(
                tool_id,
                f"args.{name} false_flag only applies to type: flag (this arg is {arg_type})",
            )

    @classmethod
    def _check_shape(
        cls,
        name: str,
        arg_type: str,
        *,
        positional: bool,
        flag: str | None,
        false_flag: str | None,
        join: str | None,
        joiner: str | None,
        repeat: bool | None,
        tool_id: str,
    ) -> None:
        if arg_type not in ARG_TYPES:
            allowed = ", ".join(sorted(ARG_TYPES))
            raise manifest_error(tool_id, f"args.{name} type {arg_type!r} not in {{{allowed}}}")
        if not positional and flag is None:
            raise manifest_error(tool_id, f"args.{name} needs a flag or positional: true")
        if positional and flag is not None:
            raise manifest_error(
                tool_id,
                f"args.{name} cannot set both flag and positional: true — argv only emits the "
                "positional (a child overriding a parent's positional must also set "
                "positional: false)",
            )
        cls._check_join(name, join, tool_id=tool_id)
        cls._check_false_flag(name, arg_type, false_flag=false_flag, tool_id=tool_id)
        if arg_type == "pairs" and flag is None:
            raise manifest_error(tool_id, f"args.{name} type: pairs needs a flag")
        cls._check_emit_conflicts(
            name,
            arg_type,
            positional=positional,
            join=join,
            joiner=joiner,
            repeat=repeat,
            tool_id=tool_id,
        )


@dataclass(slots=True, frozen=True)
class OutputSpec:
    """Typed ``output:`` block; ``success_codes`` validated at load."""

    format: str | None = None
    success_codes: tuple[int, ...] = (0,)
    from_file: str | None = None
    """When set, the parse input is this file's contents, not stdout. The
    template interpolates ``{cwd}`` and string kwargs (``"{output}/report.json"``).
    """
    allow_stale_report: bool = False
    """Accept an ``output.from_file`` report older than the run that should have
    written it. Off by default so a tool that failed to rewrite its report can
    never be parsed as this run's output; turn it on for an incremental tool
    that deliberately leaves an unchanged report alone, or on a filesystem whose
    mtime clock is offset from the host's (NFS, some container bind mounts).
    """

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> OutputSpec:
        """Parse output map; fail closed on non-maps / bad success_codes."""
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "output must be a map")
        if bad := unknown_keys(raw, OUTPUT_KEYS):
            raise manifest_error(tool_id, f"output unknown keys: {', '.join(bad)}")
        return cls(
            format=optional_str(raw.get("format")),
            success_codes=cls._parse_success_codes(raw.get("success_codes", [0]), tool_id=tool_id),
            from_file=cls._parse_from_file(raw.get("from_file"), tool_id=tool_id),
            allow_stale_report=require_bool(
                raw.get("allow_stale_report"),
                field_name="output.allow_stale_report",
                tool_id=tool_id,
            ),
        )

    @staticmethod
    def _parse_from_file(raw: Any, *, tool_id: str) -> str | None:
        """A relative-path template; absolute paths and ``..`` are rejected."""
        text = optional_str(raw)
        if text is None:
            return None
        parts = text.replace("\\", "/").split("/")
        if text.startswith("/") or ".." in parts:
            raise manifest_error(
                tool_id, f"output.from_file {text!r} must be a relative path without '..'"
            )
        return text

    @staticmethod
    def _parse_success_codes(codes: Any, *, tool_id: str) -> tuple[int, ...]:
        if not isinstance(codes, list) or not codes:
            raise manifest_error(tool_id, "output.success_codes must be a non-empty list")
        if any(isinstance(c, bool) or not isinstance(c, int) for c in codes):
            raise manifest_error(tool_id, "output.success_codes must be integers")
        return tuple(codes)


@dataclass(slots=True)
class Manifest:
    """Resolved registry surface ready for install/run."""

    id: str
    data: dict[str, Any]
    subcommands: list[str] = field(default_factory=list)
    path: Path | None = None
    parser_path: Path | None = None
    install: InstallSpec = field(default_factory=InstallSpec)
    args: dict[str, ArgSpec] = field(default_factory=dict)
    output: OutputSpec = field(default_factory=OutputSpec)
    requires: RequiresSpec = field(default_factory=RequiresSpec)

    @property
    def abstract(self) -> bool:
        return require_bool(self.data.get("abstract"), field_name="abstract", tool_id=self.id)

    @property
    def has_cli(self) -> bool:
        """Whether ``cli:`` is set. A manifest without one is inheritance-only
        (a ``base`` for ``extends``); it cannot be listed, prepared, or run."""
        return self.data.get("cli") is not None

    @property
    def cli(self) -> str:
        return safe_segment(self.data.get("cli"), field_name="cli", tool_id=self.id)

    @property
    def version(self) -> str | None:
        return safe_segment(
            self.data.get("version"), field_name="version", tool_id=self.id, allow_none=True
        )

    @property
    def parser(self) -> str | None:
        value = self.data.get("parser")
        return None if value is None else str(value)

    @property
    def separator(self) -> str | None:
        """Token emitted before the positional block (typically ``--``)."""
        value = self.data.get("separator")
        return None if value is None else str(value)

    @property
    def platforms(self) -> tuple[str, ...]:
        """Declared host platforms (``linux`` / ``macos`` / ``windows``).

        Empty tuple means "any". Enforced at resolve time so an unsupported
        host fails with a clear message instead of a missing-asset error.
        """
        raw = self.data.get("platforms")
        if raw is None:
            return ()
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise manifest_error(self.id, "platforms must be a list of strings")
        return tuple(raw)

    @property
    def env(self) -> dict[str, str]:
        """Static environment variables layered onto the tool subprocess.

        Applied after any ``env_allow`` filtering, so manifest-declared vars are
        always forwarded. Empty when the manifest has no ``env`` block.
        """
        raw = self.data.get("env")
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(self.id, "env must be a map")
        return {
            str(key): scalar_str(value, field_name=f"env.{key}", tool_id=self.id)
            for key, value in raw.items()
        }


def parse_args_map(raw: Any, *, tool_id: str) -> dict[str, ArgSpec]:
    """Parse the args map into typed ArgSpec entries.

    A ``<name>: null`` entry is an inheritance drop marker with nothing to
    drop (no parent declared it); it is ignored rather than an error.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise manifest_error(tool_id, "args must be a map")
    return {
        name: ArgSpec.from_mapping(name, spec, tool_id=tool_id)
        for name, spec in raw.items()
        if spec is not None
    }
