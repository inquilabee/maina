"""Registry load, inheritance merge, and catalog listing."""

from __future__ import annotations

import warnings
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from maina.core.errors import ManifestError

from . import ids as registry_ids
from .common import MANIFEST_PREFIX, manifest_error, scalar_str, unknown_keys
from .requires import RequiresSpec
from .specs import (
    BASE_TOOL_ID,
    CURRENT_SCHEMA,
    IGNORED_MANIFEST_KEYS,
    TOP_LEVEL_KEYS,
    VALID_PLATFORMS,
    InstallSpec,
    Manifest,
    OutputSpec,
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
        # Memo of "does <resolved root> ship a base.yaml", per root — without it
        # every load() re-scans (rglob) for the base id. Cleared on append_roots.
        self._base_present: dict[Path, bool] = {}

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
        self._base_present.clear()

    def load(self, tool_id: str) -> Manifest:
        """Load and merge a tool manifest by registry id."""
        tid = self.normalize_id(tool_id)
        chain = self._load_chain(tid, [])
        merged: dict[str, Any] = {}
        subcommands: list[str] = []
        leaf_path = chain[-1][1]
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

        decl_path, decl_root = self._parser_declaring_layer(chain, merged.get("parser"))
        parser_path = self._resolve_parser_path(decl_path, decl_root, merged.get("parser"))
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

    @staticmethod
    def _parser_declaring_layer(
        chain: list[tuple[str, Path, Path, dict[str, Any]]], parser: Any
    ) -> tuple[Path, Path]:
        """``(manifest path, owning root)`` of the layer that set ``parser:``.

        A ``parsers/*.py`` ref resolves against the family of the manifest that
        *declared* it, not the leaf — otherwise a parent's parser is unreachable
        from a child in another family.
        """
        for _cid, path, root, data in reversed(chain):
            if data.get("parser") == parser:
                return path, root
        return chain[-1][1], chain[-1][2]

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
        cls._validate_separator_and_from_file(tid, merged)
        cls._validate_scalar_fields(tid, merged)

    @staticmethod
    def _validate_scalar_fields(tid: str, merged: dict[str, Any]) -> None:
        """Force the ``bool`` / ``float`` rejection for lazily-read scalar fields.

        ``cli`` / ``version`` and every ``env`` value are read through properties;
        validate them here so an unquoted ``version: 3.10`` fails at load, not at
        the first download.
        """
        env = merged.get("env")
        scalars = {
            **{k: merged[k] for k in ("cli", "version") if merged.get(k) is not None},
            **{f"env.{k}": v for k, v in (env.items() if isinstance(env, dict) else ())},
        }
        for field_name, value in scalars.items():
            scalar_str(value, field_name=field_name, tool_id=tid)

    @classmethod
    def _validate_separator_and_from_file(cls, tid: str, merged: dict[str, Any]) -> None:
        """``separator`` shape / ``order`` conflict, and no ``cwd`` arg with ``from_file``."""
        args = merged.get("args") or {}
        cls._validate_separator(tid, merged.get("separator"), args)
        output = merged.get("output") or {}
        if isinstance(output, dict) and output.get("from_file") and "cwd" in args:
            raise manifest_error(tid, "an arg cannot be named 'cwd' when output.from_file is set")

    @staticmethod
    def _validate_separator(tid: str, sep: Any, args: Any) -> None:
        if sep is None:
            return
        if not isinstance(sep, str) or not sep:
            raise manifest_error(tid, f"separator {sep!r} must be a non-empty string")
        if not isinstance(args, dict):
            return
        if ordered := [n for n, a in args.items() if isinstance(a, dict) and "order" in a]:
            raise manifest_error(
                tid,
                "separator and args.order are two ways to lay out argv — pick one "
                f"(args with order: {', '.join(map(str, ordered))})",
            )

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
        if platforms is None:
            return
        if not isinstance(platforms, list):
            raise manifest_error(
                tid, f"platforms {platforms!r} must be a list, e.g. [linux, macos]"
            )
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
            base = next(
                (d for d in registry_ids.id_dir_candidates(root, parts) if d.is_dir()), None
            )
            if base is not None:
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
        """Collect the distinct registry ids across every root.

        This is a *set* of ids for catalog listing — it says nothing about which
        root wins a duplicate id (that precedence lives in ``_manifest_lookup``,
        which scans ``reversed(self.roots)``).
        """
        seen: dict[str, None] = {
            tid: None for root in self.roots for tid in registry_ids.iter_yaml_ids(root)
        }
        return list(seen)

    def _load_chain(
        self,
        tool_id: str,
        seen: list[str],
        *,
        root: Path | None = None,
    ) -> list[tuple[str, Path, Path, dict[str, Any]]]:
        """Load the extends chain root→leaf for a tool id.

        When a tool (other than ``base``) has no ``extends`` and **its own root**
        holds a ``base.yaml``, ``base`` from *that* root is prepended so the
        chain is ``base → … → tool``. A ``base.yaml`` in a *different* registered
        root does not leak in — each root's ``base`` scopes to its own tools.
        When *root* is set the lookup is pinned there (used to resolve ``base``
        against the owning root).
        """
        tid = self.normalize_id(tool_id)
        if tid in seen:
            cycle = " -> ".join([*seen, tid])
            raise ManifestError(f"extends cycle: {cycle}")
        path, owning_root = self._lookup_in(tid, root)
        data = self._load_raw_yaml(path)
        chain: list[tuple[str, Path, Path, dict[str, Any]]] = []
        if extends := data.get("extends"):
            parent_id = self.normalize_id(str(extends))
            chain.extend(self._load_chain(parent_id, [*seen, tid]))
        elif tid != BASE_TOOL_ID and self._root_has_base(owning_root):
            chain.extend(self._load_chain(BASE_TOOL_ID, [*seen, tid], root=owning_root))
        chain.append((tid, path, owning_root, data))
        return chain

    def _lookup_in(self, tid: str, root: Path | None) -> tuple[Path, Path]:
        """``(path, owning_root)`` for *tid* — pinned to *root* when given."""
        if root is None:
            return self._manifest_lookup(tid)
        path = registry_ids.manifest_path_in(root, tid)
        if path is None:
            raise ManifestError(f"no manifest for {tid!r} under {root}")
        return path, root

    def _root_has_base(self, root: Path) -> bool:
        """Whether *root* ships a ``base.yaml`` (memoized per resolved root)."""
        key = root.resolve()
        if key not in self._base_present:
            self._base_present[key] = registry_ids.manifest_path_in(root, BASE_TOOL_ID) is not None
        return self._base_present[key]

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

    # A manifest is a small hand-written config; a wall of YAML anchors is either
    # a mistake or an alias-expansion ("billion laughs") attempt.
    MAX_YAML_ANCHORS = 64

    @classmethod
    def _load_raw_yaml(cls, path: Path) -> dict[str, Any]:
        """Load a single YAML manifest file."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"cannot read {path}: {exc}") from exc
        if text.count("&") > cls.MAX_YAML_ANCHORS:
            raise ManifestError(f"{MANIFEST_PREFIX}{path} has too many YAML anchors")
        try:
            raw = yaml.safe_load(text)
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

    # install sub-maps that deep-merge per platform key (a child that overrides
    # one platform's asset must not drop the others).
    _INSTALL_DEEP_KEYS = ("assets", "sha256")

    @classmethod
    def _merge_map_key(
        cls, key: str, parent: dict[str, Any], child: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge a MAP_KEYS block. ``args`` merges per-arg-name then per-field;
        ``install`` shallow-merges but deep-merges ``assets`` / ``sha256``;
        ``<arg>: null`` drops an inherited arg (mirrors ``requires``)."""
        out = deepcopy(parent)
        if key == "args":
            return cls._merge_args(out, child)
        out |= deepcopy(child)
        deep_subs = cls._INSTALL_DEEP_KEYS if key == "install" else ()
        for sub in deep_subs:
            if isinstance(parent.get(sub), dict) and isinstance(child.get(sub), dict):
                out[sub] = {**parent[sub], **child[sub]}
        return out

    @staticmethod
    def _merge_args(out: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        for arg_name, child_spec in child.items():
            if child_spec is None:
                out.pop(arg_name, None)
                continue
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
