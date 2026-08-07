"""Registry discovery, inheritance merge, and hyphen-id resolution."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from pyporter.core.errors import ManifestError

if TYPE_CHECKING:
    from collections.abc import Sequence

MAP_KEYS = frozenset({"args", "install", "env", "output", "requires"})
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


class Registry:
    """Instance-scoped owner of registry overlay roots (later roots win)."""

    def __init__(self, root: Path | Sequence[Path] | None = None) -> None:
        if root is None:
            self.roots: list[Path] = []
        else:
            self.roots = [Path(root)] if isinstance(root, (str, Path)) else [Path(p) for p in root]

    @property
    def root(self) -> Path:
        """First overlay root (single-root compatibility)."""
        if not self.roots:
            raise ManifestError("registry has no roots")
        return self.roots[0]

    def append_roots(self, roots: Sequence[Path]) -> None:
        """Append overlay roots (later entries override earlier ids)."""
        self.roots.extend(Path(p) for p in roots)

    def load(self, tool_id: str) -> Manifest:
        """Load and merge a tool manifest by registry id."""
        tid = self.normalize_id(tool_id)
        chain = self._load_chain(tid, [])
        merged: dict[str, Any] = {}
        subcommands: list[str] = []
        leaf_path, leaf_root = chain[-1][1], chain[-1][2]
        for cid, _path, _root, data in chain:
            layer = deepcopy(data)
            # ``base`` is an abstract defaults bag; do not force abstract onto leaves.
            if cid == BASE_TOOL_ID and tid != BASE_TOOL_ID:
                layer.pop("abstract", None)
            # Normalize legacy requires before map-merge so base freeform pins compose.
            if "requires" in layer:
                layer["requires"] = dict(
                    RequiresSpec.from_mapping(layer["requires"], tool_id=cid).runtimes
                )
            if (seg := layer.get("subcommand")) is not None and str(seg) != "":
                subcommands.append(str(seg))
            merged = self.merge_maps(merged, layer) if merged else layer
            merged.pop("extends", None)
            merged.pop("subcommand", None)

        for key in IGNORED_MANIFEST_KEYS:
            merged.pop(key, None)

        parser_path = self._resolve_parser_path(leaf_path, leaf_root, merged.get("parser"))
        return Manifest(
            id=tid,
            data=merged,
            subcommands=subcommands,
            path=leaf_path,
            parser_path=parser_path,
            install=InstallSpec.from_mapping(merged.get("install"), tool_id=tid),
            args=Registry.parse_args_map(merged.get("args"), tool_id=tid),
            output=OutputSpec.from_mapping(merged.get("output"), tool_id=tid),
            requires=RequiresSpec.from_mapping(merged.get("requires"), tool_id=tid),
        )

    def list_commands(self, tool_id: str | None = None) -> list[str]:
        """List callable registry ids, or concrete children of a parent.

        When *tool_id* is None, returns every non-abstract id in the registry
        (sorted), including nested hyphen ids such as ``kubectl-get``. Abstract
        surfaces stay hidden from catalogs but remain loadable and may be used
        as a parent filter (``list kubectl``).
        """
        if tool_id is None or tool_id == "":
            return sorted(self._exclude_abstract(self.iter_ids()))
        tid = self.normalize_id(tool_id)
        by_id: dict[str, None] = {}
        found_any = False
        parts = tid.split("-")
        for root in self.roots:
            base = root.joinpath(*parts)
            if base.is_dir():
                found_any = True
                for child_id in self._list_children(base, tid):
                    by_id[child_id] = None
            elif self._manifest_path_in(root, tid) is not None:
                found_any = True
        if not found_any:
            self._manifest_lookup(tid)
            return []
        return sorted(self._exclude_abstract(list(by_id)))

    def _exclude_abstract(self, ids: list[str]) -> list[str]:
        """Drop inheritance-only surfaces from a catalog listing."""
        return [tid for tid in ids if not self.load(tid).abstract]

    def iter_ids(self) -> list[str]:
        """Collect all registry ids across roots (later root wins duplicates)."""
        seen: dict[str, None] = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*.yaml")):
                if "parsers" in path.relative_to(root).parts:
                    continue
                seen[self.yaml_path_to_id(path, root)] = None
        return list(seen)

    def _load_chain(
        self,
        tool_id: str,
        seen: list[str],
    ) -> list[tuple[str, Path, Path, dict[str, Any]]]:
        """Load the extends chain root→leaf for a tool id.

        When a tool (other than ``base``) has no ``extends`` and a ``base``
        manifest exists, ``base`` is prepended so the chain is
        ``base → … → tool``. Parents that themselves lack ``extends`` also
        pick up ``base``, so ``extends: foo`` still yields ``base → foo → tool``.
        """
        tid = self.normalize_id(tool_id)
        if tid in seen:
            cycle = " -> ".join([*seen, tid])
            raise ManifestError(f"extends cycle: {cycle}")
        path, owning_root = self._manifest_lookup(tid)
        data = self._load_raw_yaml(path)
        chain: list[tuple[str, Path, Path, dict[str, Any]]] = []
        if extends := data.get("extends"):
            parent_id = self.normalize_id(str(extends))
            chain.extend(self._load_chain(parent_id, [*seen, tid]))
        elif tid != BASE_TOOL_ID and self._has_manifest(BASE_TOOL_ID):
            chain.extend(self._load_chain(BASE_TOOL_ID, [*seen, tid]))
        chain.append((tid, path, owning_root, data))
        return chain

    def _has_manifest(self, tool_id: str) -> bool:
        """Return True when *tool_id* resolves under any registry root."""
        if not self.roots:
            return False
        tid = self.normalize_id(tool_id)
        return any(self._manifest_path_in(root, tid) is not None for root in self.roots)

    def _manifest_lookup(self, tool_id: str) -> tuple[Path, Path]:
        """Resolve id to (manifest path, owning root). Later roots win."""
        tid = self.normalize_id(tool_id)
        if not tid:
            raise ManifestError("empty tool id")
        if not self.roots:
            raise ManifestError(f"no registry entry for {tid!r} (registry has no roots)")
        for root in reversed(self.roots):
            if (path := self._manifest_path_in(root, tid)) is not None:
                return path, root
        roots = ", ".join(str(r) for r in self.roots)
        raise ManifestError(f"no registry entry for {tid!r} under {roots}")

    @staticmethod
    def _manifest_path_in(root: Path, tool_id: str) -> Path | None:
        """Resolve a hyphen registry id under one root, or None if missing.

        Path segments must not contain ``-``. Tries, in order:
        ``root/{tid}.yaml``, ``root/a/b/c.yaml`` for ``a-b-c``, and family
        ``root/a/b/c/c.yaml`` (including ``root/kubectl/kubectl.yaml``).
        """
        direct = root / f"{tool_id}.yaml"
        if direct.is_file():
            return direct
        parts = tool_id.split("-")
        if not parts or parts == [""]:
            return None
        nested = root.joinpath(*parts).with_suffix(".yaml")
        if nested.is_file():
            return nested
        family = root.joinpath(*parts, parts[-1]).with_suffix(".yaml")
        return family if family.is_file() else None

    def _resolve_parser_path(
        self,
        leaf_path: Path,
        leaf_root: Path,
        parser: Any,
    ) -> Path | None:
        """Resolve a ``*.py`` parser under the winning leaf's family ``parsers/``."""
        if not parser or not isinstance(parser, str):
            return None
        if not parser.endswith(".py"):
            return None
        family_parsers = self._family_parsers_dir(leaf_path, leaf_root)
        candidate = family_parsers / parser
        if candidate.is_file():
            return candidate
        raise ManifestError(f"parser {parser!r} not found under {family_parsers}")

    @staticmethod
    def _family_parsers_dir(leaf_path: Path, leaf_root: Path) -> Path:
        """Nearest parsers/ directory walking up from the leaf toward its root."""
        current = leaf_path.parent
        resolved_root = leaf_root.resolve()
        while True:
            parsers = current / "parsers"
            if parsers.is_dir():
                return parsers
            if current.resolve() == resolved_root or current.parent == current:
                return parsers
            current = current.parent

    def _list_children(self, base: Path, prefix: str) -> list[str]:
        """List child registry ids under a directory surface."""
        return [
            child_id
            for path in sorted(base.iterdir())
            if (child_id := self._child_entry_id(base, prefix, path)) is not None
        ]

    @staticmethod
    def parse_args_map(raw: Any, *, tool_id: str) -> dict[str, ArgSpec]:
        """Parse the args map into typed ArgSpec entries."""
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ManifestError(f"{MANIFEST_PREFIX}{tool_id!r} args must be a map")
        return {
            name: ArgSpec.from_mapping(name, spec, tool_id=tool_id) for name, spec in raw.items()
        }

    @staticmethod
    def _load_raw_yaml(path: Path) -> dict[str, Any]:
        """Load a single YAML manifest file."""
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ManifestError(f"cannot read {path}: {exc}") from exc
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {path}: {exc}") from exc
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ManifestError(f"{MANIFEST_PREFIX}{path} must be a mapping")
        return raw

    @staticmethod
    def _child_entry_id(base: Path, prefix: str, path: Path) -> str | None:
        """Return a child registry id for a path under base, or None if skipped."""
        return (
            None
            if path.name in {"parsers"} or path.name.startswith(".")
            else (
                (None if path.stem == base.name else f"{prefix}-{path.stem}")
                if path.is_file() and path.suffix == ".yaml"
                else (
                    f"{prefix}-{path.name}"
                    if path.is_dir() and Registry._dir_has_manifests(path)
                    else None
                )
            )
        )

    @staticmethod
    def _dir_has_manifests(path: Path) -> bool:
        """True when a directory contains YAML manifests."""
        nested = path / f"{path.name}.yaml"
        return nested.is_file() or any(path.glob("*.yaml"))

    @staticmethod
    def normalize_id(tool_id: str) -> str:
        """Normalize a registry id to hyphen form (strip .yaml, unify separators)."""
        cleaned = tool_id.strip().replace("\\", "-").replace("/", "-").lstrip("-")
        if cleaned.endswith(".yaml"):
            cleaned = cleaned[: -len(".yaml")]
        elif cleaned.endswith(".yml"):
            cleaned = cleaned[: -len(".yml")]
        return cleaned

    @staticmethod
    def join_tool_id(name: str, subcommand: str | None = None) -> str:
        """Join Tool(name, subcommand=...) into a hyphen id."""
        base = Registry.normalize_id(name)
        return f"{base}-{Registry.normalize_id(subcommand)}" if subcommand else base

    @staticmethod
    def merge_maps(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge child onto parent using registry inheritance rules."""
        out = deepcopy(parent)
        for key, value in child.items():
            if key in {"subcommand", "extends"}:
                continue
            if key in MAP_KEYS and isinstance(value, dict) and isinstance(out.get(key), dict):
                merged = deepcopy(out[key])
                merged |= deepcopy(value)
                out[key] = merged
                continue
            out[key] = deepcopy(value)
        return out

    @staticmethod
    def yaml_path_to_id(path: Path, root: Path) -> str:
        """Convert a registry YAML path to a hyphen id."""
        parts = list(path.relative_to(root).with_suffix("").parts)
        family = len(parts) >= 2 and parts[-1] == parts[-2]
        return "-".join(parts[:-1]) if family else "-".join(parts)
