"""Registry domain types: args, install, requires, output, and Manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from maina.core.errors import ManifestError

if TYPE_CHECKING:
    from pathlib import Path

# Dropped from the merged manifest before use (pure catalog decoration).
# ``platforms`` is kept and enforced at resolve time (see ``Manifest.platforms``).
IGNORED_MANIFEST_KEYS = frozenset({"name"})
MANIFEST_PREFIX = "manifest "
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
        "default",
        "description",
        "help",
    }
)
ARG_TYPES = frozenset({"string", "flag", "path"})
INSTALL_KEYS = frozenset({"backend", "repo", "assets", "binary", "tag", "package", "sha256"})


def is_unsafe_segment(text: str) -> bool:
    return (
        not text
        or text != text.strip()
        or text.startswith(".")  # covers "." and ".."
        or any(char in text for char in "/\\\x00")
    )


def safe_segment(value: Any, *, field_name: str, tool_id: str, allow_none: bool = False) -> Any:
    """Reject a manifest value that is not a single safe path segment.

    ``cli`` / ``version`` / ``install.binary`` are interpolated into cache
    paths; ``..``, separators, a leading ``.`` or a NUL would let a manifest
    write outside the cache root.
    """
    if value is None:
        if allow_none:
            return None
        raise manifest_error(tool_id, f"missing {field_name}")
    text = str(value)
    if is_unsafe_segment(text):
        raise manifest_error(tool_id, f"{field_name} {text!r} is not a safe path segment")
    return text


# Legacy singular form only — freeform pins use runtime names as keys.
LEGACY_REQUIRES_KEYS = frozenset({"runtime", "version"})
REQUIRES_KEYS = LEGACY_REQUIRES_KEYS  # back-compat alias for importers
OUTPUT_KEYS = frozenset({"format", "success_codes"})


def optional_str(value: Any) -> str | None:
    """Coerce optional YAML scalars to str, preserving None."""
    return None if value is None else str(value)


def manifest_error(subject: str, detail: str) -> ManifestError:
    """A ``ManifestError`` for *subject* (a tool id) with a consistent prefix."""
    return ManifestError(f"{MANIFEST_PREFIX}{subject!r} {detail}")


def unknown_keys(raw: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    """Return sorted unknown mapping keys relative to an allowlist."""
    return sorted(key for key in raw if key not in allowed)


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
    default: Any = field(default=None, hash=False, compare=False)
    has_default: bool = False
    description: str | None = None
    help: str | None = None

    @classmethod
    def from_mapping(cls, name: str, raw: Any, *, tool_id: str) -> ArgSpec:
        """Parse one arg map; fail closed on non-maps, unknown keys, and bad shapes."""
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, f"args.{name} must be a map")
        if bad := unknown_keys(raw, ARG_KEYS):
            raise manifest_error(tool_id, f"args.{name} unknown keys: {', '.join(bad)}")
        arg_type = str(raw.get("type", "string"))
        positional = raw.get("positional", False) is True
        flag = None if raw.get("flag") is None else str(raw["flag"])
        cls._check_shape(name, arg_type, positional=positional, flag=flag, tool_id=tool_id)
        return cls(
            type=arg_type,
            flag=flag,
            joiner=None if raw.get("joiner") is None else str(raw["joiner"]),
            positional=positional,
            required=raw.get("required", False) is True,
            repeat=raw.get("repeat"),
            join=None if raw.get("join") is None else str(raw["join"]),
            default=raw.get("default"),
            has_default="default" in raw,
            description=optional_str(raw.get("description")),
            help=optional_str(raw.get("help")),
        )

    @staticmethod
    def _check_shape(
        name: str, arg_type: str, *, positional: bool, flag: str | None, tool_id: str
    ) -> None:
        if arg_type not in ARG_TYPES:
            allowed = ", ".join(sorted(ARG_TYPES))
            raise manifest_error(tool_id, f"args.{name} type {arg_type!r} not in {{{allowed}}}")
        if not positional and flag is None:
            raise manifest_error(tool_id, f"args.{name} needs a flag or positional: true")


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
        assets = raw.get("assets") or {}
        if not isinstance(assets, dict):
            raise manifest_error(tool_id, "install.assets must be a map")
        backend = raw.get("backend", "path")
        return cls(
            backend="path" if backend is None else str(backend),
            repo=optional_str(raw.get("repo")),
            assets=dict(assets),
            binary=safe_segment(
                raw.get("binary"), field_name="install.binary", tool_id=tool_id, allow_none=True
            ),
            tag=optional_str(raw.get("tag")),
            package=optional_str(raw.get("package")),
            sha256=cls._parse_sha256(raw.get("sha256"), tool_id=tool_id),
        )

    @staticmethod
    def _parse_sha256(raw: Any, *, tool_id: str) -> dict[str, str]:
        """Parse the optional ``install.sha256`` map (platform key → hex digest)."""
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(
                tool_id, "install.sha256 must be a map of platform key to hex digest"
            )
        return {str(key): str(value) for key, value in raw.items()}


@dataclass(slots=True, frozen=True)
class RequiresSpec:
    """Typed ``requires:`` map (runtime→version). Legacy ``runtime``+``version`` supported.

    Inheritance: child pins merge onto parents; ``requires: null`` replaces (clears)
    inherited pins; a freeform ``runtime: null`` drops that pin from the merge.
    """

    runtimes: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        """Return the pinned version for *name*, or None."""
        return self.runtimes.get(name)

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> RequiresSpec:
        """Parse requires map; fail closed on non-maps and mixed forms.

        ``None`` means no pins (used after ``requires: null`` clears inheritance).
        """
        mapping = cls._require_map(raw, tool_id=tool_id)
        legacy = cls._legacy_keys(mapping, tool_id=tool_id)
        return (
            cls._from_legacy(mapping, tool_id=tool_id)
            if legacy
            else cls._from_pins(mapping, tool_id=tool_id)
        )

    @classmethod
    def layer_for_merge(cls, raw: Any, *, tool_id: str) -> dict[str, str | None] | None:
        """Normalize a layer's ``requires`` for inheritance merge.

        Returns ``None`` when the layer sets ``requires: null`` (replace/clear).
        Freeform ``runtime: null`` values are preserved as drop markers.
        """
        if raw is None:
            return None
        mapping = cls._require_map(raw, tool_id=tool_id)
        legacy = cls._legacy_keys(mapping, tool_id=tool_id)
        return (
            dict(cls._from_legacy(mapping, tool_id=tool_id).runtimes)
            if legacy
            else cls._pins_for_merge(mapping, tool_id=tool_id)
        )

    @staticmethod
    def _require_map(raw: Any, *, tool_id: str) -> dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "requires must be a map")
        return raw

    @staticmethod
    def _legacy_keys(raw: dict[str, Any], *, tool_id: str) -> set[str]:
        keys = set(raw)
        legacy, freeform = keys & LEGACY_REQUIRES_KEYS, keys - LEGACY_REQUIRES_KEYS
        if legacy and freeform:
            mixed = ", ".join(sorted(str(key) for key in freeform))
            raise manifest_error(
                tool_id,
                f"requires cannot mix legacy runtime/version with freeform pins ({mixed})",
            )
        return legacy

    @classmethod
    def _from_legacy(cls, raw: dict[str, Any], *, tool_id: str) -> RequiresSpec:
        """Parse legacy singular ``runtime`` / ``version`` form."""
        if bad := unknown_keys(raw, LEGACY_REQUIRES_KEYS):
            joined = ", ".join(bad)
            raise manifest_error(tool_id, f"requires unknown keys: {joined}")
        runtime = optional_str(raw.get("runtime"))
        version_raw = raw.get("version")
        version = (
            None
            if version_raw is None
            else cls._version_scalar(version_raw, tool_id=tool_id, key="version")
        )
        return cls() if runtime is None else cls(runtimes={runtime: version or "lts"})

    @classmethod
    def _from_pins(cls, raw: dict[str, Any], *, tool_id: str) -> RequiresSpec:
        """Parse freeform runtime→version pins (final resolved map; no nulls)."""
        pins: dict[str, str] = {}
        for name, value in raw.items():
            cls._require_runtime_key(name, tool_id=tool_id)
            if value is None:
                raise ManifestError(
                    cls._pin_message(
                        tool_id,
                        name,
                        "must be a version string (use requires: null to clear inherited pins)",
                    )
                )
            pins[name] = cls._version_scalar(value, tool_id=tool_id, key=name)
        return cls(runtimes=pins)

    @classmethod
    def _pins_for_merge(cls, raw: dict[str, Any], *, tool_id: str) -> dict[str, str | None]:
        """Parse freeform pins for merge; ``null`` values mark pins to drop."""
        pins: dict[str, str | None] = {}
        for name, value in raw.items():
            cls._require_runtime_key(name, tool_id=tool_id)
            pins[name] = (
                None if value is None else cls._version_scalar(value, tool_id=tool_id, key=name)
            )
        return pins

    @staticmethod
    def _require_runtime_key(name: Any, *, tool_id: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise manifest_error(tool_id, "requires keys must be non-empty strings")

    @staticmethod
    def _pin_message(tool_id: str, key: str, detail: str) -> str:
        return f"{MANIFEST_PREFIX}{tool_id!r} requires.{key} {detail}"

    @classmethod
    def _version_scalar(cls, value: Any, *, tool_id: str, key: str) -> str:
        """Coerce a version pin; reject YAML floats and non-str/int scalars.

        Unquoted YAML ``3.10`` becomes float ``3.1`` — authors must quote pins
        (``python: "3.10"``).
        """
        if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
            kind = type(value).__name__
            raise ManifestError(
                cls._pin_message(
                    tool_id,
                    key,
                    f'must be a version string (quote pins like "3.10"; got {kind})',
                )
            )
        if isinstance(value, float):
            raise ManifestError(
                cls._pin_message(
                    tool_id,
                    key,
                    f"must be a quoted version string (YAML float {value!r} loses precision; "
                    f'quote like {key}: "3.10")',
                )
            )
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value
        raise ManifestError(
            cls._pin_message(
                tool_id,
                key,
                f"must be a version string (got {type(value).__name__})",
            )
        )


@dataclass(slots=True, frozen=True)
class OutputSpec:
    """Typed ``output:`` block; ``success_codes`` validated at load."""

    format: str | None = None
    success_codes: tuple[int, ...] = (0,)

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> OutputSpec:
        """Parse output map; fail closed on non-maps / bad success_codes."""
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "output must be a map")
        if bad := unknown_keys(raw, OUTPUT_KEYS):
            keys = ", ".join(bad)
            raise manifest_error(tool_id, f"output unknown keys: {keys}")
        codes = raw.get("success_codes", [0])
        if not isinstance(codes, list) or not codes:
            raise manifest_error(tool_id, "output.success_codes must be a non-empty list")
        parsed: list[int] = []
        for item in codes:
            if isinstance(item, bool) or not isinstance(item, int):
                raise manifest_error(tool_id, "output.success_codes must be integers")
            parsed.append(item)
        return cls(
            format=optional_str(raw.get("format")),
            success_codes=tuple(parsed),
        )


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
        return self.data.get("abstract") is True

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
        return {str(key): str(value) for key, value in raw.items()}


def parse_args_map(raw: Any, *, tool_id: str) -> dict[str, ArgSpec]:
    """Parse the args map into typed ArgSpec entries."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise manifest_error(tool_id, "args must be a map")
    return {name: ArgSpec.from_mapping(name, spec, tool_id=tool_id) for name, spec in raw.items()}
