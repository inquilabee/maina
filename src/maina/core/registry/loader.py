"""Registry load, inheritance merge, and catalog listing."""

from __future__ import annotations

import warnings
from copy import deepcopy
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

import yaml

from maina.core.errors import ManifestError
from maina.core.subprocess_utils import resolve_within

from . import ids as registry_ids
from .common import MANIFEST_PREFIX, scalar_str
from .install import InstallSpec
from .keys import BASE_TOOL_ID, IGNORED_MANIFEST_KEYS
from .merge import merge_maps
from .requires import RequiresSpec
from .specs import Manifest, OutputSpec, parse_args_map
from .validate import (
    MAX_YAML_ANCHORS,
    count_yaml_anchors,
    validate_relative_ref,
    validate_top_level,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


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
            given = [root] if isinstance(root, (str, Path)) else list(root)
            self.roots = [Path(p).resolve() for p in given]
        # Memo of "does <resolved root> ship a base.yaml", per root — without it
        # every load() re-scans (rglob) for the base id. Cleared on append_roots.
        self._base_present: dict[Path, bool] = {}
        # Per-root (id, path) pairs from one rglob, built lazily on first use
        # and reused by every id lookup, `iter_ids`, and `duplicate_ids`
        # (`load()` / `list` / `validate` would otherwise rescan the tree once
        # per id). `_scan_index` is the derived id -> first-path map. Both
        # cleared on append_roots.
        self._id_paths: dict[Path, list[tuple[str, Path]]] = {}
        self._scan_index: dict[Path, dict[str, Path]] = {}
        # Parsed YAML per (path, mtime_ns, size) — `Tool(id)` otherwise re-parses
        # every layer of the extends chain on every construction, and
        # `list_commands` parses the whole catalog to filter abstracts. Keyed on
        # the file's own stamp, so an edited manifest re-parses on its own.
        self._raw_yaml: dict[tuple[str, int, int], dict[str, Any]] = {}
        # Built Manifest per (id, stamp of every file in its extends chain) —
        # without this, `list_commands(None)` re-deepcopies, re-merges, and
        # re-validates every id's whole chain on every call via
        # `_exclude_abstract`, and a repeated `Tool(id)` does the same. Keying
        # on every layer's own stamp (not just the leaf's) means an edited
        # parent invalidates every child's cached Manifest too.
        self._manifests: dict[tuple[str, tuple[tuple[str, int, int], ...]], Manifest] = {}

    @property
    def root(self) -> Path:
        """First overlay root (single-root compatibility)."""
        if not self.roots:
            raise ManifestError("registry has no roots")
        return self.roots[0]

    def append_roots(self, roots: Sequence[Path]) -> None:
        """Append overlay roots (later entries override earlier ids).

        Roots are stored absolute: a relative root would make a ``script`` tool's
        ``argv[0]`` relative and break any run with an explicit ``cwd``. A root
        already present is not added again.
        """
        seen = set(self.roots)
        for raw in roots:
            path = Path(raw).resolve()
            if path not in seen:
                self.roots.append(path)
                seen.add(path)
        self.refresh()

    def refresh(self) -> None:
        """Forget which manifest files each root holds, so ones added since the
        last lookup are seen.

        A ``Registry`` scans each root once and reuses that index for every id
        lookup, ``iter_ids``, and ``list_commands``. In a long-lived process (a
        service, a notebook, a watch loop, a test that writes manifests into an
        already-registered root) a file added afterwards is otherwise invisible
        to listing — and, for an id only the tree scan can resolve, unloadable.
        Call this after writing manifests into a registered root. Adding a root
        refreshes on its own.
        """
        self._base_present.clear()
        self._id_paths.clear()
        self._scan_index.clear()
        self._raw_yaml.clear()
        self._manifests.clear()

    def load(self, tool_id: str) -> Manifest:
        """Load and merge a tool manifest by registry id.

        A problem found while merging or validating is re-raised naming the
        file(s) it came from: the id is derived from a path, and for a defect
        inherited from a shared parent it points at the wrong manifest entirely.

        The built ``Manifest`` is cached on the id plus every chain layer's
        own stamp (see ``_manifests``), so a repeated ``load()`` of the same
        id — as ``list_commands(None)`` does for every id in the catalog — is
        O(1) after the first, not a full re-deepcopy/re-merge/re-validate.
        """
        tid = self.normalize_id(tool_id)
        chain = self._load_chain(tid, [])
        cache_key = (tid, tuple(self._stat_key(path) for _cid, path, _root, _data in chain))
        if cache_key in self._manifests:
            return self._manifests[cache_key]
        try:
            manifest = self._build(tid, chain)
        except ManifestError as exc:
            raise self._located(exc, chain) from exc
        self._manifests[cache_key] = manifest
        return manifest

    @staticmethod
    def _stat_key(path: Path) -> tuple[str, int, int]:
        """``(path, mtime_ns, size)`` — the same file-identity stamp used for
        the raw-YAML cache, reused here so the Manifest cache invalidates
        exactly when a re-parse would have produced a different result."""
        try:
            stat = path.stat()
        except OSError as exc:
            raise ManifestError(f"cannot read {path}: {exc}") from exc
        return (str(path), stat.st_mtime_ns, stat.st_size)

    @staticmethod
    def _located(
        exc: ManifestError, chain: list[tuple[str, Path, Path, dict[str, Any]]]
    ) -> ManifestError:
        """*exc* with the chain's manifest files appended (leaf first)."""
        if exc.files:
            return exc
        paths = [str(path) for _cid, path, _root, _data in reversed(chain)]
        leaf, parents = paths[0], paths[1:]
        where = f"{leaf}, which extends {', '.join(parents)}" if parents else leaf
        return ManifestError(f"{exc} (in {where})", files=tuple(paths))

    def _build(self, tid: str, chain: list[tuple[str, Path, Path, dict[str, Any]]]) -> Manifest:
        """Merge a loaded ``extends`` chain root→leaf into one ``Manifest``."""
        merged: dict[str, Any] = {}
        subcommands: list[str] = []
        leaf_path = chain[-1][1]
        for index, (cid, _path, _root, data) in enumerate(chain):
            layer = deepcopy(data)  # the raw layer is memoized and shared; never mutate it
            # ``abstract`` is never inherited: a surface is abstract only when its
            # own leaf manifest declares it (like ``extends`` / ``subcommand``).
            if index != len(chain) - 1:
                layer.pop("abstract", None)
            # Normalize legacy→freeform before merge; preserve null clear / pin drops.
            if "requires" in layer:
                layer["requires"] = RequiresSpec.layer_for_merge(layer["requires"], tool_id=cid)
            if (seg := layer.get("subcommand")) is not None and str(seg) != "":
                subcommands.append(scalar_str(seg, field_name="subcommand", tool_id=cid))
            merged = merge_maps(merged, layer) if merged else layer
            merged.pop("extends", None)
            merged.pop("subcommand", None)

        for key in IGNORED_MANIFEST_KEYS:
            merged.pop(key, None)
        validate_top_level(tid, merged)

        decl_path, decl_root = self._declaring_layer(
            chain, lambda d: d.get("parser") == merged.get("parser")
        )
        parser_path = self._resolve_parser_path(decl_path, decl_root, merged.get("parser"))

        install_spec = InstallSpec.from_mapping(merged.get("install"), tool_id=tid)
        entry_path = None
        if install_spec.backend == "script":
            entry_decl_path, entry_decl_root = self._declaring_layer(
                chain, lambda d: (d.get("install") or {}).get("entry") == install_spec.entry
            )
            entry_path = self._resolve_entry_path(
                entry_decl_path, entry_decl_root, install_spec.entry
            )

        return Manifest(
            id=tid,
            # Registry.load caches and reuses this instance across calls; a
            # plain dict would let a caller mutate .data and corrupt every
            # other load() of the same id (see Manifest.data's docstring).
            data=MappingProxyType(merged),
            subcommands=subcommands,
            path=leaf_path,
            parser_path=parser_path,
            entry_path=entry_path,
            install=install_spec,
            args=self.parse_args_map(merged.get("args"), tool_id=tid),
            output=OutputSpec.from_mapping(merged.get("output"), tool_id=tid),
            requires=RequiresSpec.from_mapping(merged.get("requires"), tool_id=tid),
        )

    @staticmethod
    def _declaring_layer(
        chain: list[tuple[str, Path, Path, dict[str, Any]]],
        matches: Callable[[dict[str, Any]], bool],
    ) -> tuple[Path, Path]:
        """``(manifest path, owning root)`` of the layer for which *matches* is true.

        A family-relative ref (``parser:``, ``install.entry``) resolves against
        the family of the manifest that *declared* it, not the leaf — otherwise
        a parent's ref is unreachable from a child in another family.
        """
        for _cid, path, root, data in reversed(chain):
            if matches(data):
                return path, root
        return chain[-1][1], chain[-1][2]

    def list_commands(self, tool_id: str | None = None) -> list[str]:
        """List callable registry ids, or concrete descendants of a parent.

        When *tool_id* is None, returns every non-abstract id in the registry
        (sorted), including nested hyphen ids such as ``kubectl-get``. Abstract
        surfaces stay hidden from catalogs but remain loadable and may be used
        as a parent filter (``list kubectl``). A descendant is any id whose
        hyphen segments start with the parent's — nested at any depth, so a
        directory that groups manifests without a manifest of its own (no
        ``foo.yaml`` for a ``family/foo/`` directory) does not hide what is
        under it, unlike inventing and trying to load an id for that directory.
        """
        if tool_id is None or tool_id == "":
            return sorted(self._exclude_abstract(self.iter_ids()))
        tid = self.normalize_id(tool_id)
        prefix = f"{tid}-"
        descendants = [sid for sid in self.iter_ids() if sid.startswith(prefix)]
        if not descendants and not self._has_manifest(tid):
            self._manifest_lookup(tid)  # raises: no registry entry for tid
        return sorted(self._exclude_abstract(descendants))

    def _exclude_abstract(self, ids: list[str]) -> list[str]:
        """Drop inheritance-only surfaces from a catalog listing.

        A manifest that fails to load is skipped with a warning rather than
        aborting the whole listing — one bad file must not hide every good one.
        """
        out: list[str] = []
        for tid in ids:
            try:
                manifest = self.load(tid)
                if not manifest.abstract and manifest.has_cli:
                    out.append(tid)
            except ManifestError as exc:
                warnings.warn(f"skipping {tid!r}: {exc}", RuntimeWarning, stacklevel=2)
        return out

    def iter_ids(self) -> list[str]:
        """Collect the distinct registry ids across every root.

        This is a *set* of ids for catalog listing — it says nothing about which
        root wins a duplicate id (that precedence lives in ``_manifest_lookup``,
        which scans ``reversed(self.roots)``). A same-root collision (two files
        deriving the one id) warns — see :meth:`duplicate_ids`.
        """
        seen: dict[str, None] = {}
        for root in self.roots:
            for tid, paths in self._root_duplicate_ids(root).items():
                files = ", ".join(str(p) for p in paths)
                warnings.warn(
                    f"{tid!r} is derived from {len(paths)} manifests under {root}: "
                    f"{files} — only one is reachable",
                    RuntimeWarning,
                    stacklevel=2,
                )
            seen |= dict.fromkeys(tid for tid, _path in self._root_id_paths(root))
        return list(seen)

    def duplicate_ids(self) -> dict[str, list[Path]]:
        """Same-root id collisions across every root — see :meth:`iter_ids`."""
        out: dict[str, list[Path]] = {}
        for root in self.roots:
            out |= self._root_duplicate_ids(root)
        return out

    def _root_duplicate_ids(self, root: Path) -> dict[str, list[Path]]:
        """Ids derived from more than one manifest file under *root*."""
        by_id: dict[str, list[Path]] = {}
        for tid, path in self._root_id_paths(root):
            by_id.setdefault(tid, []).append(path)
        return {tid: paths for tid, paths in by_id.items() if len(paths) > 1}

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
        path = self._locate(root, tid)
        if path is None:
            raise ManifestError(f"no manifest for {tid!r} under {root}")
        return path, root

    def _locate(self, root: Path, tid: str) -> Path | None:
        """Fast split-on-``-`` candidates first; a miss falls back to a
        per-root scan index built once (not a fresh ``rglob`` per id)."""
        if (path := registry_ids.fast_candidate_path(root, tid)) is not None:
            return path
        return self._root_scan_index(root).get(tid)

    def _root_id_paths(self, root: Path) -> list[tuple[str, Path]]:
        """``(id, path)`` pairs under *root* — one ``rglob``, cached per root."""
        key = root.resolve()
        if key not in self._id_paths:
            self._id_paths[key] = registry_ids.iter_yaml_id_paths(root)
        return self._id_paths[key]

    def _root_scan_index(self, root: Path) -> dict[str, Path]:
        """id -> first-matching-path (sorted-rglob order) under *root*, memoized."""
        key = root.resolve()
        if key not in self._scan_index:
            index: dict[str, Path] = {}
            for tid, path in self._root_id_paths(root):
                index.setdefault(tid, path)  # first match wins (sorted-rglob order)
            self._scan_index[key] = index
        return self._scan_index[key]

    def _root_has_base(self, root: Path) -> bool:
        """Whether *root* ships a ``base.yaml`` (memoized per resolved root)."""
        key = root.resolve()
        if key not in self._base_present:
            self._base_present[key] = self._locate(root, BASE_TOOL_ID) is not None
        return self._base_present[key]

    def _has_manifest(self, tool_id: str) -> bool:
        """Return True when *tool_id* resolves under any registry root."""
        if not self.roots:
            return False
        tid = self.normalize_id(tool_id)
        return any(self._locate(root, tid) is not None for root in self.roots)

    def _manifest_lookup(self, tool_id: str) -> tuple[Path, Path]:
        """Resolve id to (manifest path, owning root). Later roots win."""
        tid = self.normalize_id(tool_id)
        if not tid:
            raise ManifestError("empty tool id")
        if not self.roots:
            raise ManifestError(
                f"no registry entry for {tid!r} (registry has no roots — set "
                "MAINA_REGISTRY=<dir> or call maina.register(registry_dirs=[...]))"
            )
        for root in reversed(self.roots):
            if (path := self._locate(root, tid)) is not None:
                return path, root
        roots = ", ".join(str(r) for r in self.roots)
        raise ManifestError(f"no registry entry for {tid!r} under {roots}")

    @staticmethod
    def _resolve_family_or_root_ref(
        decl_path: Path, decl_root: Path, ref: str, *, family_dirname: str
    ) -> Path:
        """Resolve *ref* to an absolute path: a bare name under the declaring
        layer's family *family_dirname*/, or a ``/``-containing path under its
        registry root. Raises ``ManifestError`` if it would escape that base
        (the resolved path is exec'd, so a manifest must not point it outside
        its registry root)."""
        base = (
            decl_root
            if "/" in ref
            else registry_ids.family_dir(decl_path, decl_root, family_dirname)
        )
        candidate = base / ref
        resolve_within(candidate, base, error_cls=ManifestError, message=f"{ref!r} escapes {base}")
        return candidate

    @staticmethod
    def _resolve_parser_path(
        decl_path: Path,
        decl_root: Path,
        parser: Any,
    ) -> Path | None:
        """Resolve a ``*.py`` parser ref — see ``validate_relative_ref``."""
        if not parser or not isinstance(parser, str):
            return None
        if not parser.endswith(".py"):
            return None
        validate_relative_ref(parser, kind="parser")
        candidate = Registry._resolve_family_or_root_ref(
            decl_path, decl_root, parser, family_dirname="parsers"
        )
        if candidate.is_file():
            return candidate
        raise ManifestError(f"parser {parser!r} not found under {candidate.parent}")

    @staticmethod
    def _resolve_entry_path(
        decl_path: Path,
        decl_root: Path,
        entry: Any,
    ) -> Path | None:
        """Resolve an ``install.entry`` ref — see ``validate_relative_ref``.

        A single file or a directory (a script package); the ``script`` install
        backend decides which shape it is and how to run it at resolve time.
        """
        if not entry or not isinstance(entry, str):
            return None
        validate_relative_ref(entry, kind="script entry")
        candidate = Registry._resolve_family_or_root_ref(
            decl_path, decl_root, entry, family_dirname="scripts"
        )
        if candidate.is_file() or candidate.is_dir():
            return candidate
        raise ManifestError(f"script entry {entry!r} not found under {candidate.parent}")

    def _load_raw_yaml(self, path: Path) -> dict[str, Any]:
        """Load a single YAML manifest file, memoized on its own mtime + size.

        The returned mapping is shared with every other loader of the same file;
        callers deep-copy before merging (see :meth:`_build`).
        """
        key = self._stat_key(path)
        if key not in self._raw_yaml:
            self._raw_yaml[key] = self._parse_yaml(path)
        return self._raw_yaml[key]

    @staticmethod
    def _parse_yaml(path: Path) -> dict[str, Any]:
        """Read and parse one YAML manifest file."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ManifestError(f"cannot read {path}: {exc}") from exc
        try:
            anchor_count = count_yaml_anchors(text)
        except yaml.YAMLError as exc:
            raise ManifestError(f"invalid YAML in {path}: {exc}") from exc
        if anchor_count > MAX_YAML_ANCHORS:
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
