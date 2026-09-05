"""Hyphen-id normalization and registry path discovery helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maina.core.errors import ManifestError

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

UNSAFE_ID_SEGMENTS = frozenset({"", ".", ".."})
ID_SUFFIXES = (".yaml", ".yml")
FAMILY_DIR_NAMES = frozenset({"parsers", "scripts"})


def is_manifest_path(rel: Path) -> bool:
    """True when *rel* (a path relative to a registry root) is a tool manifest.

    Excludes ``parsers/`` / ``scripts/`` contents and anything under a
    dot-directory (``.github/``, ``.git/``, …) so a registry root that is
    also a repo checkout does not surface CI workflows as tool ids.
    """
    parts = rel.parts
    return (
        rel.suffix in ID_SUFFIXES
        and FAMILY_DIR_NAMES.isdisjoint(parts)
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


def fast_candidate_path(root: Path, tool_id: str) -> Path | None:
    """The split-on-``-`` guesses for *tool_id* under *root* — no directory
    walk, so this is cheap enough to retry per lookup. ``None`` on a miss (the
    id may still resolve via a tree scan; see ``Registry._root_scan_index``)."""
    parts = tool_id.split("-")
    if not parts or any(part in UNSAFE_ID_SEGMENTS for part in parts):
        return None
    root_resolved = root.resolve()
    for candidate in id_candidate_paths(root, tool_id, parts):
        if candidate.is_file() and candidate.resolve().is_relative_to(root_resolved):
            return candidate
    return None


def family_dir(leaf_path: Path, leaf_root: Path, name: str) -> Path:
    """Nearest *name*/ directory (``"parsers"``, ``"scripts"``) walking up from
    the leaf toward its root."""
    current = leaf_path.parent
    resolved_root = leaf_root.resolve()
    while True:
        candidate = current / name
        if candidate.is_dir():
            return candidate
        if current.resolve() == resolved_root or current.parent == current:
            return candidate
        current = current.parent


def iter_yaml_id_paths(root: Path) -> list[tuple[str, Path]]:
    """``(id, path)`` for every manifest under *root* (skip parsers/, dot-dirs)."""
    return (
        [
            (yaml_path_to_id(path, root), path)
            for path in sorted(root.rglob("*"))
            if is_manifest_path(path.relative_to(root))
        ]
        if root.is_dir()
        else []
    )
