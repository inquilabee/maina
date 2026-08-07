"""Registry load, inheritance merge, and catalog listing."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from maina.core.errors import ManifestError

from . import ids as registry_ids
from .specs import (
    BASE_TOOL_ID,
    IGNORED_MANIFEST_KEYS,
    MANIFEST_PREFIX,
    InstallSpec,
    Manifest,
    OutputSpec,
    RequiresSpec,
    parse_args_map,
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
            args=self.parse_args_map(merged.get("args"), tool_id=tid),
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
                for child_id in registry_ids.list_child_ids(base, tid):
                    by_id[child_id] = None
            elif registry_ids.manifest_path_in(root, tid) is not None:
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
        family_parsers = registry_ids.family_parsers_dir(leaf_path, leaf_root)
        candidate = family_parsers / parser
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
