"""Hyphen-id normalization and registry path discovery helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maina.core.errors import ManifestError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

UNSAFE_ID_SEGMENTS = frozenset({"", ".", ".."})
ID_SUFFIXES = (".yaml", ".yml")


def is_manifest_path(rel: Path) -> bool:
    """True when *rel* (a path relative to a registry root) is a tool manifest.

    Excludes ``parsers/`` modules and anything under a dot-directory
    (``.github/``, ``.git/``, …) so a registry root that is also a repo
    checkout does not surface CI workflows as tool ids.
    """
    parts = rel.parts
    return (
        rel.suffix in ID_SUFFIXES
        and "parsers" not in parts
        and all(not part.startswith(".") for part in parts)
    )


def strip_yaml_suffix(text: str) -> str:
    for suffix in ID_SUFFIXES:
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def collapse_trailing_dupes(parts: list[str]) -> list[str]:
    while len(parts) >= 2 and parts[-1] == parts[-2]:
        parts.pop()
    return parts


def normalize_id(tool_id: str) -> str:
    """Normalize a registry id to hyphen form (strip .yaml, unify separators).

    Trailing duplicate segments (``kubectl-kubectl``, ``git-remote-remote``) are
    collapsed so family-root paths and ``extends`` share one canonical id.
    Segments that could escape a registry root (``.``, ``..``) are rejected.
    """
    cleaned = strip_yaml_suffix(tool_id.strip().replace("\\", "-").replace("/", "-").lstrip("-"))
    parts = [p for p in cleaned.split("-") if p]
    if any(part in UNSAFE_ID_SEGMENTS for part in parts):
        raise ManifestError(f"unsafe tool id {tool_id!r}: '.'/'..' segments are not allowed")
    return "-".join(collapse_trailing_dupes(parts))


def join_tool_id(name: str, subcommand: str | None = None) -> str:
    """Join Tool(name, subcommand=...) into a hyphen id."""
    base = normalize_id(name)
    return f"{base}-{normalize_id(subcommand)}" if subcommand else base


def yaml_path_to_id(path: Path, root: Path) -> str:
    """Convert a registry YAML path to a canonical hyphen id.

    Routed through :func:`normalize_id` so a hyphen *inside* a filename
    (``foo-foo.yaml``) collapses the same way ``normalize_id`` collapses a
    trailing-dupe id — otherwise the file would be enumerated but unloadable.
    """
    parts = list(path.relative_to(root).with_suffix("").parts)
    family = len(parts) >= 2 and parts[-1] == parts[-2]
    return normalize_id("-".join(parts[:-1]) if family else "-".join(parts))


def id_candidate_paths(root: Path, tool_id: str, parts: list[str]) -> Iterator[Path]:
    """Fast split-on-``-`` path guesses for a hyphen id under *root*."""
    for suffix in ID_SUFFIXES:
        yield root / f"{tool_id}{suffix}"
        yield root.joinpath(*parts).with_suffix(suffix)
        yield root.joinpath(*parts, parts[-1]).with_suffix(suffix)


def scan_for_id(root: Path, tool_id: str) -> Path | None:
    """Walk *root* for the manifest whose derived id equals *tool_id*."""
    if not root.is_dir():
        return None
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if is_manifest_path(rel) and yaml_path_to_id(path, root) == tool_id:
            return path
    return None


def manifest_path_in(root: Path, tool_id: str) -> Path | None:
    """Resolve a hyphen registry id under one root, or None if missing.

    Tries the fast split-on-``-`` candidates first, then falls back to scanning
    the tree so a hyphenated filename in a subdirectory (``family/my-tool.yaml``
    → id ``family-my-tool``) still resolves.
    """
    parts = tool_id.split("-")
    if not parts or any(part in UNSAFE_ID_SEGMENTS for part in parts):
        return None
    root_resolved = root.resolve()
    for candidate in id_candidate_paths(root, tool_id, parts):
        if candidate.is_file() and candidate.resolve().is_relative_to(root_resolved):
            return candidate
    return scan_for_id(root, tool_id)


def family_parsers_dir(leaf_path: Path, leaf_root: Path) -> Path:
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


def dir_has_manifests(path: Path) -> bool:
    """True when a directory contains YAML manifests."""
    return any(
        (path / f"{path.name}{suffix}").is_file() or any(path.glob(f"*{suffix}"))
        for suffix in ID_SUFFIXES
    )


def child_entry_id(base: Path, prefix: str, path: Path) -> str | None:
    """Return a child registry id for a path under base, or None if skipped."""
    return (
        None
        if path.name in {"parsers"} or path.name.startswith(".")
        else (
            (None if path.stem == base.name else f"{prefix}-{path.stem}")
            if path.is_file() and path.suffix in ID_SUFFIXES
            else (f"{prefix}-{path.name}" if path.is_dir() and dir_has_manifests(path) else None)
        )
    )


def list_child_ids(base: Path, prefix: str) -> list[str]:
    """List child registry ids under a directory surface."""
    return [
        child_id
        for path in sorted(base.iterdir())
        if (child_id := child_entry_id(base, prefix, path)) is not None
    ]


def iter_yaml_ids(root: Path) -> list[str]:
    """Collect registry ids from YAML files under one root (skip parsers/, dot-dirs)."""
    return (
        [
            yaml_path_to_id(path, root)
            for path in sorted(root.rglob("*"))
            if is_manifest_path(path.relative_to(root))
        ]
        if root.is_dir()
        else []
    )
