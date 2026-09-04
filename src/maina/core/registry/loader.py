"""Registry load, inheritance merge, and catalog listing."""

from __future__ import annotations

import warnings
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from maina.core.errors import ManifestError

from . import ids as registry_ids
from .specs import (
    BASE_TOOL_ID,
    CURRENT_SCHEMA,
    IGNORED_MANIFEST_KEYS,
    MANIFEST_PREFIX,
    TOP_LEVEL_KEYS,
    VALID_PLATFORMS,
    InstallSpec,
    Manifest,
    OutputSpec,
    RequiresSpec,
    manifest_error,
    parse_args_map,
    unknown_keys,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

MAP_KEYS = frozenset({"args", "install", "env", "output", "requires"})


class Registry:
    """Instance-scoped owner of registry overlay roots (later roots win)."""

    normalize_id = staticmethod(registry_ids.normalize_id)
    join_tool_id = staticmethod(registry_ids.join_tool_id)
    yaml_path_to_id = staticmethod(registry_ids.yaml_path_to_id)
    parse_args_map = staticmethod(parse_args_map)

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
        """Append overlay roots (later entries override earlier ids).

        A root already present (by resolved path) is not added again.
        """
        seen = {p.resolve() for p in self.roots}
        for raw in roots:
            path = Path(raw)
            if path.resolve() not in seen:
                self.roots.append(path)
                seen.add(path.resolve())

    def load(self, tool_id: str) -> Manifest:
        """Load and merge a tool manifest by registry id."""
        tid = self.normalize_id(tool_id)
        chain = self._load_chain(tid, [])
        merged: dict[str, Any] = {}
        subcommands: list[str] = []
        leaf_path, leaf_root = chain[-1][1], chain[-1][2]
        for index, (cid, _path, _root, data) in enumerate(chain):
            layer = deepcopy(data)
            # ``abstract`` is never inherited: a surface is abstract only when its
            # own leaf manifest declares it (like ``extends`` / ``subcommand``).
            if index != len(chain) - 1:
                layer.pop("abstract", None)
            # Normalize legacy→freeform before merge; preserve null clear / pin drops.
            if "requires" in layer:
                layer["requires"] = RequiresSpec.layer_for_merge(layer["requires"], tool_id=cid)
            if (seg := layer.get("subcommand")) is not None and str(seg) != "":
                subcommands.append(str(seg))
            merged = self.merge_maps(merged, layer) if merged else layer
            merged.pop("extends", None)
            merged.pop("subcommand", None)

        for key in IGNORED_MANIFEST_KEYS:
            merged.pop(key, None)
        self._validate_top_level(tid, merged)

        parser_path = self._resolve_parser_path(leaf_path, leaf_root, merged.get("parser"))
        return Manifest(
            id=tid,
            data=merged,
            subcommands=subcommands,
            path=leaf_path,
            parser_path=parser_path,
            install=InstallSpec.from_mapping(merged.get("install"), tool_id=tid),
            args=self.parse_args_map(merged.get("args"), tool_id=tid),
            output=OutputSpec.from_mapping(merged.get("output"), tool_id=tid),
            requires=RequiresSpec.from_mapping(merged.get("requires"), tool_id=tid),
        )

    @classmethod
    def _validate_top_level(cls, tid: str, merged: dict[str, Any]) -> None:
        """Fail closed on unknown keys and bad schema / timeout / platforms values."""
        if bad := unknown_keys(merged, TOP_LEVEL_KEYS):
            raise manifest_error(tid, f"unknown top-level keys: {', '.join(bad)}")
        schema = merged.get("schema")
        if schema is not None and schema != CURRENT_SCHEMA:
            raise manifest_error(
                tid, f"schema {schema!r} unsupported (maina understands schema {CURRENT_SCHEMA})"
            )
        cls._validate_timeout(tid, merged.get("timeout"))
        cls._validate_platforms(tid, merged.get("platforms"))

    @staticmethod
    def _validate_timeout(tid: str, timeout: Any) -> None:
        if timeout is None:
            return
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise manifest_error(tid, f"timeout {timeout!r} must be a number of seconds")
        if timeout <= 0:
            raise manifest_error(tid, f"timeout {timeout!r} must be > 0 (omit it for no limit)")

    @staticmethod
    def _validate_platforms(tid: str, platforms: Any) -> None:
        if not isinstance(platforms, list):
            return
        if unknown := sorted(str(p) for p in platforms if p not in VALID_PLATFORMS):
            allowed = ", ".join(sorted(VALID_PLATFORMS))
            raise manifest_error(tid, f"platforms {unknown} not in {{{allowed}}}")

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
                for child_id in registry_ids.list_child_ids(base, tid):
                    by_id[child_id] = None
            elif registry_ids.manifest_path_in(root, tid) is not None:
                found_any = True
        if not found_any:
            self._manifest_lookup(tid)
            return []
        return sorted(self._exclude_abstract(list(by_id)))

    def _exclude_abstract(self, ids: list[str]) -> list[str]:
        """Drop inheritance-only surfaces from a catalog listing.

        A manifest that fails to load is skipped with a warning rather than
        aborting the whole listing — one bad file must not hide every good one.
        """
        out: list[str] = []
        for tid in ids:
            try:
                if not self.load(tid).abstract:
                    out.append(tid)
            except ManifestError as exc:
                warnings.warn(f"skipping {tid!r}: {exc}", RuntimeWarning, stacklevel=2)
        return out

    def iter_ids(self) -> list[str]:
        """Collect all registry ids across roots (later root wins duplicates)."""
        # Later roots overwrite earlier ids via comprehension insertion order.
        seen: dict[str, None] = {
            tid: None for root in self.roots for tid in registry_ids.iter_yaml_ids(root)
        }
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
        return any(registry_ids.manifest_path_in(root, tid) is not None for root in self.roots)

    def _manifest_lookup(self, tool_id: str) -> tuple[Path, Path]:
        """Resolve id to (manifest path, owning root). Later roots win."""
        tid = self.normalize_id(tool_id)
        if not tid:
            raise ManifestError("empty tool id")
        if not self.roots:
            raise ManifestError(f"no registry entry for {tid!r} (registry has no roots)")
        for root in reversed(self.roots):
            if (path := registry_ids.manifest_path_in(root, tid)) is not None:
                return path, root
        roots = ", ".join(str(r) for r in self.roots)
        raise ManifestError(f"no registry entry for {tid!r} under {roots}")

    @staticmethod
    def _validate_parser_ref(parser: str) -> None:
        """A ``parsers/`` ref is a plain relative filename — no ``..``, no abs path.

        The resolved file is ``exec``'d, so a manifest must not be able to point
        it outside the family ``parsers/`` directory.
        """
        parts = parser.replace("\\", "/").split("/")
        if (
            parser != parser.strip()
            or "\x00" in parser
            or parser.startswith("/")
            or ".." in parts
            or Path(parser).is_absolute()
        ):
            raise ManifestError(
                f"parser {parser!r} must be a plain relative filename under parsers/"
            )

    @staticmethod
    def _resolve_parser_path(
        leaf_path: Path,
        leaf_root: Path,
        parser: Any,
    ) -> Path | None:
        """Resolve a ``*.py`` parser under the winning leaf's family ``parsers/``."""
        if not parser or not isinstance(parser, str):
            return None
        if not parser.endswith(".py"):
            return None
        Registry._validate_parser_ref(parser)
        family_parsers = registry_ids.family_parsers_dir(leaf_path, leaf_root)
        candidate = family_parsers / parser
        if not candidate.resolve().is_relative_to(family_parsers.resolve()):
            raise ManifestError(f"parser {parser!r} escapes {family_parsers}")
        if candidate.is_file():
            return candidate
        raise ManifestError(f"parser {parser!r} not found under {family_parsers}")

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
    def merge_maps(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge child onto parent using registry inheritance rules.

        ``requires: null`` replaces inherited pins. Within a requires map,
        ``runtime: null`` drops that pin; other map keys still shallow-merge.
        """
        out = deepcopy(parent)
        for key, value in child.items():
            if key in {"subcommand", "extends"}:
                continue
            if key == "requires":
                out[key] = Registry._merge_requires(out.get(key), value)
                continue
            if key in MAP_KEYS and isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = Registry._merge_map_key(key, out[key], value)
                continue
            out[key] = deepcopy(value)
        return out

    @staticmethod
    def _merge_map_key(key: str, parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Merge a MAP_KEYS block. ``args`` merges per-arg-name then per-field so
        a child overriding one field (e.g. ``default:``) keeps the parent's
        ``flag`` / ``type`` / ``required``; other blocks shallow-merge."""
        out = deepcopy(parent)
        if key != "args":
            out |= deepcopy(child)
            return out
        for arg_name, child_spec in child.items():
            mergeable = isinstance(child_spec, dict) and isinstance(out.get(arg_name), dict)
            out[arg_name] = {**out[arg_name], **child_spec} if mergeable else deepcopy(child_spec)
        return out

    @staticmethod
    def _merge_requires(parent: Any, child: Any) -> Any:
        """Merge requires maps; ``None`` child clears; null pin values drop keys."""
        if child is None:
            return None
        if not isinstance(child, dict):
            return deepcopy(child)
        merged: dict[str, Any] = deepcopy(parent) if isinstance(parent, dict) else {}
        for name, version in child.items():
            if version is None:
                merged.pop(name, None)
            else:
                merged[name] = deepcopy(version)
        return merged
