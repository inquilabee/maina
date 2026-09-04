"""Registry domain types: args, install, requires, output, and Manifest."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .common import (
    is_unsafe_segment,
    manifest_error,
    optional_str,
    require_bool,
    require_optional_bool,
    safe_segment,
    scalar_str,
    unknown_keys,
)
from .requires import RequiresSpec

if TYPE_CHECKING:
    from pathlib import Path

# Dropped from the merged manifest before use (pure catalog decoration).
# ``platforms`` is kept and enforced at resolve time (see ``Manifest.platforms``).
IGNORED_MANIFEST_KEYS = frozenset({"name"})
BASE_TOOL_ID = "base"
CURRENT_SCHEMA = 1

# Every key a merged manifest may carry. Validated at load so a typo
# (``outputs:``, ``platform:``, ``timeoutt:``) fails closed like the nested
# blocks, instead of loading silently and being ignored.
TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "cli",
        "version",
        "subcommand",
        "extends",
        "abstract",
        "platforms",
        "timeout",
        "separator",
        "parser",
        "env",
        "args",
        "install",
        "requires",
        "output",
        "name",
    }
)

ARG_KEYS = frozenset(
    {
        "type",
        "flag",
        "joiner",
        "positional",
        "required",
        "repeat",
        "join",
        "order",
        "default",
        "description",
        "help",
        "choices",
    }
)
ARG_TYPES = frozenset({"string", "flag", "path", "pairs"})
JOIN_VALUES = frozenset({"csv"})
VALID_PLATFORMS = frozenset({"linux", "macos", "windows"})
INSTALL_KEYS = frozenset(
    {
        "backend",
        "repo",
        "assets",
        "binary",
        "tag",
        "package",
        "sha256",
        "allow_scripts",
        "allow_any_executable",
    }
)


OUTPUT_KEYS = frozenset({"format", "success_codes", "from_file"})


@dataclass(slots=True, frozen=True)
class ArgSpec:
    """One registry arg entry (kwargs → argv schema).

    ``type: path`` is emit-only metadata for help/docs; values are still
    stringified like other non-flag types (no ``Path`` coercion).
    """

    type: str = "string"
    flag: str | None = None
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
        join = None if raw.get("join") is None else str(raw["join"])
        cls._check_shape(
            name, arg_type, positional=positional, flag=flag, join=join, tool_id=tool_id
        )
        return cls(
            type=arg_type,
            flag=flag,
            joiner=cls._opt_scalar(raw, "joiner", name=name, tool_id=tool_id),
            positional=positional,
            required=require_bool(
                raw.get("required"), field_name=f"args.{name}.required", tool_id=tool_id
            ),
            repeat=require_optional_bool(
                raw.get("repeat"), field_name=f"args.{name}.repeat", tool_id=tool_id
            ),
            join=join,
            order=cls._parse_order(raw.get("order"), name=name, tool_id=tool_id),
            default=cls._scalar_default(raw, arg_type, name=name, tool_id=tool_id),
            has_default="default" in raw,
            description=optional_str(raw.get("description")),
            help=optional_str(raw.get("help")),
            choices=cls._parse_choices(raw, arg_type, name=name, tool_id=tool_id),
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
    def _check_shape(
        name: str,
        arg_type: str,
        *,
        positional: bool,
        flag: str | None,
        join: str | None,
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
        if join is not None and join not in JOIN_VALUES:
            raise manifest_error(tool_id, f"args.{name} join {join!r} not in {{csv}}")
        if arg_type == "pairs" and flag is None:
            raise manifest_error(tool_id, f"args.{name} type: pairs needs a flag")


@dataclass(slots=True, frozen=True)
class InstallSpec:
    """Typed ``install:`` block from a resolved manifest."""

    backend: str = "path"
    repo: str | None = None
    assets: dict[str, Any] = field(default_factory=dict)
    binary: str | None = None
    tag: str | None = None
    package: str | None = None
    sha256: dict[str, str] = field(default_factory=dict)
    allow_scripts: bool = False
    allow_any_executable: bool = False

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> InstallSpec:
        """Parse install map; fail closed on non-maps and unknown keys."""
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "install must be a map")
        if bad := unknown_keys(raw, INSTALL_KEYS):
            keys = ", ".join(bad)
            raise manifest_error(tool_id, f"install unknown keys: {keys}")
        return cls(
            backend="path" if raw.get("backend") is None else str(raw.get("backend", "path")),
            repo=cls._parse_repo(raw.get("repo"), tool_id=tool_id),
            assets=cls._parse_assets(raw.get("assets"), tool_id=tool_id),
            binary=safe_segment(
                raw.get("binary"), field_name="install.binary", tool_id=tool_id, allow_none=True
            ),
            tag=safe_segment(
                raw.get("tag"), field_name="install.tag", tool_id=tool_id, allow_none=True
            ),
            package=cls._parse_package(raw.get("package"), tool_id=tool_id),
            sha256=cls._parse_sha256(raw.get("sha256"), tool_id=tool_id),
            allow_scripts=require_bool(
                raw.get("allow_scripts"), field_name="install.allow_scripts", tool_id=tool_id
            ),
            allow_any_executable=require_bool(
                raw.get("allow_any_executable"),
                field_name="install.allow_any_executable",
                tool_id=tool_id,
            ),
        )

    def identity_key(self, *, extra: tuple[Any, ...] = ()) -> str:
        """Short stable hash of what this spec fetches (repo/tag/asset/sha256).

        Two manifests that share ``cli`` + ``version`` but differ here must not
        share a cache directory. *extra* lets the caller fold in context the
        spec does not own (e.g. the ``requires:`` runtime pins for ``npm``).
        """
        payload = repr(
            (
                self.backend,
                self.repo,
                self.tag,
                sorted(self.assets.items()),
                sorted(self.sha256.items()),
                self.package,
                self.binary,
                self.allow_scripts,
                self.allow_any_executable,
                extra,
            )
        )
        return hashlib.blake2s(payload.encode("utf-8"), digest_size=5).hexdigest()

    # npm package: optional @scope/, then a name of url-safe lowercase chars.
    _NPM_NAME_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")

    @classmethod
    def _parse_package(cls, raw: Any, *, tool_id: str) -> str | None:
        """Validate ``install.package`` as an npm package name (scoped ok)."""
        if raw is None:
            return None
        text = scalar_str(raw, field_name="install.package", tool_id=tool_id)
        if not cls._NPM_NAME_RE.match(text):
            raise manifest_error(tool_id, f"install.package {text!r} is not a valid npm name")
        return text

    @staticmethod
    def _parse_repo(raw: Any, *, tool_id: str) -> str | None:
        if raw is None:
            return None
        text = str(raw)
        parts = text.split("/")
        if len(parts) != 2 or not all(parts) or any(is_unsafe_segment(p) for p in parts):
            raise manifest_error(tool_id, f"install.repo {text!r} must be 'owner/name'")
        return text

    @staticmethod
    def _parse_assets(raw: Any, *, tool_id: str) -> dict[str, Any]:
        assets = raw or {}
        if not isinstance(assets, dict):
            raise manifest_error(tool_id, "install.assets must be a map")
        return {
            str(key): safe_segment(value, field_name=f"install.assets.{key}", tool_id=tool_id)
            for key, value in assets.items()
        }

    @staticmethod
    def _parse_sha256(raw: Any, *, tool_id: str) -> dict[str, str]:
        """Parse the optional ``install.sha256`` map (platform key → 64-hex digest)."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(
                tool_id, "install.sha256 must be a map of platform key to hex digest"
            )
        out: dict[str, str] = {}
        for key, value in raw.items():
            digest = str(value).strip().lower()
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise manifest_error(
                    tool_id, f"install.sha256.{key} {value!r} is not a 64-char hex digest"
                )
            out[str(key)] = digest
        return out


@dataclass(slots=True, frozen=True)
class OutputSpec:
    """Typed ``output:`` block; ``success_codes`` validated at load."""

    format: str | None = None
    success_codes: tuple[int, ...] = (0,)
    from_file: str | None = None
    """When set, the parse input is this file's contents, not stdout. The
    template interpolates ``{cwd}`` and string kwargs (``"{output}/report.json"``).
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


# Names ``Tool.run`` / ``__call__`` bind as their own parameters — a manifest
# arg with one of these names is still valid, but the caller must pass it via
# ``run(args={"env": ...})`` rather than ``run(env=...)``.
RESERVED_RUN_PARAMS = frozenset(
    {"extra", "parser", "cwd", "timeout", "env_allow", "env", "stdin", "text", "args"}
)


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
