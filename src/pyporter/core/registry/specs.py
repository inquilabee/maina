"""Registry domain types: args, install, requires, output, and Manifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pyporter.core.errors import ManifestError

if TYPE_CHECKING:
    from pathlib import Path

# Catalog decoration only — not enforced at resolve/run time.
IGNORED_MANIFEST_KEYS = frozenset({"platforms", "name"})
MANIFEST_PREFIX = "manifest "
BASE_TOOL_ID = "base"

ARG_KEYS = frozenset(
    {
        "type",
        "flag",
        "positional",
        "required",
        "repeat",
        "join",
        "default",
        "description",
        "help",
    }
)
INSTALL_KEYS = frozenset({"backend", "repo", "assets", "binary", "tag", "package"})
# Legacy singular form only — freeform pins use runtime names as keys.
LEGACY_REQUIRES_KEYS = frozenset({"runtime", "version"})
REQUIRES_KEYS = LEGACY_REQUIRES_KEYS  # back-compat alias for importers
OUTPUT_KEYS = frozenset({"format", "success_codes"})


def optional_str(value: Any) -> str | None:
    """Coerce optional YAML scalars to str, preserving None."""
    return None if value is None else str(value)


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
        """Parse one arg map; fail closed on non-maps and unknown keys."""
        if not isinstance(raw, dict):
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} args.{name} must be a map")
        if bad := unknown_keys(raw, ARG_KEYS):
            keys = ", ".join(bad)
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} args.{name} unknown keys: {keys}")
        return cls(
            type=str(raw.get("type", "string")),
            flag=None if raw.get("flag") is None else str(raw["flag"]),
            positional=raw.get("positional", False),
            required=raw.get("required", False),
            repeat=raw.get("repeat"),
            join=None if raw.get("join") is None else str(raw["join"]),
            default=raw.get("default"),
            has_default="default" in raw,
            description=optional_str(raw.get("description")),
            help=optional_str(raw.get("help")),
        )


@dataclass(slots=True, frozen=True)
class InstallSpec:
    """Typed ``install:`` block from a resolved manifest."""

    backend: str = "path"
    repo: str | None = None
    assets: dict[str, Any] = field(default_factory=dict)
    binary: str | None = None
    tag: str | None = None
    package: str | None = None

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> InstallSpec:
        """Parse install map; fail closed on non-maps and unknown keys."""
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} install must be a map")
        if bad := unknown_keys(raw, INSTALL_KEYS):
            keys = ", ".join(bad)
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} install unknown keys: {keys}")
        assets = raw.get("assets") or {}
        if not isinstance(assets, dict):
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} install.assets must be a map")
        backend = raw.get("backend", "path")
        return cls(
            backend="path" if backend is None else str(backend),
            repo=optional_str(raw.get("repo")),
            assets=dict(assets),
            binary=optional_str(raw.get("binary")),
            tag=optional_str(raw.get("tag")),
            package=optional_str(raw.get("package")),
        )


@dataclass(slots=True, frozen=True)
class RequiresSpec:
    """Typed ``requires:`` map (runtime→version). Legacy ``runtime``+``version`` supported."""

    runtimes: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        """Return the pinned version for *name*, or None."""
        return self.runtimes.get(name)

    def version_for(self, name: str) -> str | None:
        """Alias for :meth:`get` (installer / npm call sites)."""
        return self.get(name)

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> RequiresSpec:
        """Parse requires map; fail closed on non-maps and mixed forms."""
        raw = {} if raw is None else raw
        if not isinstance(raw, dict):
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} requires must be a map")
        keys = set(raw)
        legacy, freeform = keys & LEGACY_REQUIRES_KEYS, keys - LEGACY_REQUIRES_KEYS
        if legacy and freeform:
            raise ManifestError(
                f"{MANIFEST_PREFIX}{tool_id!r} requires cannot mix legacy "
                f"runtime/version with freeform pins ({', '.join(sorted(freeform))})"
            )
        return (
            cls._from_legacy(raw, tool_id=tool_id)
            if legacy
            else cls._from_pins(raw, tool_id=tool_id)
        )

    @classmethod
    def _from_legacy(cls, raw: dict[str, Any], *, tool_id: str) -> RequiresSpec:
        """Parse legacy singular ``runtime`` / ``version`` form."""
        if bad := unknown_keys(raw, LEGACY_REQUIRES_KEYS):
            joined = ", ".join(bad)
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} requires unknown keys: {joined}")
        runtime, version = optional_str(raw.get("runtime")), optional_str(raw.get("version"))
        return cls() if runtime is None else cls(runtimes={runtime: version or "lts"})

    @classmethod
    def _from_pins(cls, raw: dict[str, Any], *, tool_id: str) -> RequiresSpec:
        """Parse freeform runtime→version pins."""
        pins: dict[str, str] = {}
        for name, value in raw.items():
            if not isinstance(name, str) or not name.strip():
                raise ManifestError(
                    f"{MANIFEST_PREFIX}{tool_id!r} requires keys must be non-empty strings"
                )
            if value is None or isinstance(value, (dict, list, bool)):
                raise ManifestError(
                    f"{MANIFEST_PREFIX}{tool_id!r} requires.{name} must be a version string"
                )
            pins[name] = str(value)
        return cls(runtimes=pins)


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
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} output must be a map")
        if bad := unknown_keys(raw, OUTPUT_KEYS):
            keys = ", ".join(bad)
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} output unknown keys: {keys}")
        codes = raw.get("success_codes", [0])
        if not isinstance(codes, list) or not codes:
            raise ManifestError(
                f"{MANIFEST_PREFIX}{tool_id!r} output.success_codes must be a non-empty list"
            )
        parsed: list[int] = []
        for item in codes:
            if isinstance(item, bool) or not isinstance(item, int):
                raise ManifestError(
                    f"{MANIFEST_PREFIX}{tool_id!r} output.success_codes must be integers"
                )
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
        cli = self.data.get("cli")
        if not cli:
            raise ManifestError(f"{MANIFEST_PREFIX}{self.id!r} missing cli")
        return str(cli)

    @property
    def version(self) -> str | None:
        value = self.data.get("version")
        return None if value is None else str(value)

    @property
    def parser(self) -> str | None:
        value = self.data.get("parser")
        return None if value is None else str(value)


def parse_args_map(raw: Any, *, tool_id: str) -> dict[str, ArgSpec]:
    """Parse the args map into typed ArgSpec entries."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} args must be a map")
    return {name: ArgSpec.from_mapping(name, spec, tool_id=tool_id) for name, spec in raw.items()}
