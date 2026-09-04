"""Registry domain types: args, install, requires, output, and Manifest."""

from __future__ import annotations

import hashlib
import re
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
        "separator",
        "positional",
        "required",
        "repeat",
        "join",
        "default",
        "description",
        "help",
    }
)
ARG_TYPES = frozenset({"string", "flag", "path", "pairs"})
JOIN_VALUES = frozenset({"csv"})
VALID_PLATFORMS = frozenset({"linux", "macos", "windows"})
INSTALL_KEYS = frozenset(
    {"backend", "repo", "assets", "binary", "tag", "package", "sha256", "allow_scripts"}
)


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


OUTPUT_KEYS = frozenset({"format", "success_codes", "from_file"})


def optional_str(value: Any) -> str | None:
    """Coerce optional YAML scalars to str, preserving None."""
    return None if value is None else str(value)


def manifest_error(subject: str, detail: str) -> ManifestError:
    """A ``ManifestError`` for *subject* (a tool id) with a consistent prefix."""
    return ManifestError(f"{MANIFEST_PREFIX}{subject!r} {detail}")


def unknown_keys(raw: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    """Return sorted unknown mapping keys relative to an allowlist.

    Keys are stringified first: YAML 1.1 turns ``on:`` / ``yes:`` / ``no:`` /
    ``off:`` into booleans and ``1:`` into an int, so a raw ``sorted()`` over
    mixed key types would raise ``TypeError`` — which is not a ``ManifestError``
    and would escape every load guard. Stringifying keeps such a manifest a
    clean, skippable load error.
    """
    return sorted(str(key) for key in raw if key not in allowed)


@dataclass(slots=True, frozen=True)
class ArgSpec:
    """One registry arg entry (kwargs → argv schema).

    ``type: path`` is emit-only metadata for help/docs; values are still
    stringified like other non-flag types (no ``Path`` coercion).
    """

    type: str = "string"
    flag: str | None = None
    joiner: str | None = None
    separator: str | None = None
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
        join = None if raw.get("join") is None else str(raw["join"])
        cls._check_shape(
            name, arg_type, positional=positional, flag=flag, join=join, tool_id=tool_id
        )
        return cls(
            type=arg_type,
            flag=flag,
            joiner=None if raw.get("joiner") is None else str(raw["joiner"]),
            separator=None if raw.get("separator") is None else str(raw["separator"]),
            positional=positional,
            required=raw.get("required", False) is True,
            repeat=raw.get("repeat"),
            join=join,
            default=raw.get("default"),
            has_default="default" in raw,
            description=optional_str(raw.get("description")),
            help=optional_str(raw.get("help")),
        )

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
            allow_scripts=raw.get("allow_scripts", False) is True,
        )

    def identity_key(self) -> str:
        """Short stable hash of what this spec fetches (repo/tag/asset/sha256).

        Two manifests that share ``cli`` + ``version`` but differ here must not
        share a cache directory.
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
            )
        )
        return hashlib.blake2s(payload.encode("utf-8"), digest_size=5).hexdigest()

    # npm package: optional @scope/, then a name of url-safe lowercase chars.
    _NPM_NAME_RE = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")

    @classmethod
    def _parse_package(cls, raw: Any, *, tool_id: str) -> str | None:
        """Validate ``install.package`` as an npm package name (scoped ok)."""
        text = optional_str(raw)
        if text is None:
            return None
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
    """Typed ``requires:`` map — runtime name → version string.

    Inheritance: child pins merge onto parents; ``requires: null`` replaces (clears)
    inherited pins; a ``<runtime>: null`` drops that one pin from the merge.
    """

    runtimes: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        """Return the pinned version for *name*, or None."""
        return self.runtimes.get(name)

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> RequiresSpec:
        """Parse a ``requires`` map (runtime → version); fail closed on non-maps.

        ``None`` means no pins (used after ``requires: null`` clears inheritance).
        """
        return cls._from_pins(cls._require_map(raw, tool_id=tool_id), tool_id=tool_id)

    @classmethod
    def layer_for_merge(cls, raw: Any, *, tool_id: str) -> dict[str, str | None] | None:
        """Normalize a layer's ``requires`` for inheritance merge.

        Returns ``None`` when the layer sets ``requires: null`` (replace/clear).
        A ``<runtime>: null`` value is preserved as a drop marker.
        """
        return (
            None
            if raw is None
            else cls._pins_for_merge(cls._require_map(raw, tool_id=tool_id), tool_id=tool_id)
        )

    @staticmethod
    def _require_map(raw: Any, *, tool_id: str) -> dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "requires must be a map")
        return raw

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

    # A runtime name reaches ``mise`` argv and a ``command -v`` fallback; keep it
    # to characters that cannot carry a shell or path surprise.
    _RUNTIME_KEY_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")

    @classmethod
    def _require_runtime_key(cls, name: Any, *, tool_id: str) -> None:
        if not isinstance(name, str) or not cls._RUNTIME_KEY_RE.match(name):
            raise manifest_error(tool_id, "requires keys must match [A-Za-z0-9_.+-]+")

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
