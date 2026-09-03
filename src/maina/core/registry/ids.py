"""Hyphen-id normalization and registry path discovery helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from maina.core.errors import ManifestError

if TYPE_CHECKING:
    from pathlib import Path

UNSAFE_ID_SEGMENTS = frozenset({"", ".", ".."})
ID_SUFFIXES = (".yaml", ".yml")


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
    """Convert a registry YAML path to a hyphen id."""
    parts = list(path.relative_to(root).with_suffix("").parts)
    family = len(parts) >= 2 and parts[-1] == parts[-2]
    return "-".join(parts[:-1]) if family else "-".join(parts)


def manifest_path_in(root: Path, tool_id: str) -> Path | None:
    """Resolve a hyphen registry id under one root, or None if missing.

    Path segments must not contain ``-``. Tries, in order:
    ``root/{tid}.yaml``, ``root/a/b/c.yaml`` for ``a-b-c``, and family
    ``root/a/b/c/c.yaml`` (including ``root/kubectl/kubectl.yaml``).
    """
    parts = tool_id.split("-")
    if not parts or any(part in UNSAFE_ID_SEGMENTS for part in parts):
        return None
    candidates = (
        root / f"{tool_id}.yaml",
        root.joinpath(*parts).with_suffix(".yaml"),
        root.joinpath(*parts, parts[-1]).with_suffix(".yaml"),
    )
    root_resolved = root.resolve()
    for candidate in candidates:
        if candidate.is_file() and candidate.resolve().is_relative_to(root_resolved):
            return candidate
    return None


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
    nested = path / f"{path.name}.yaml"
    return nested.is_file() or any(path.glob("*.yaml"))


def child_entry_id(base: Path, prefix: str, path: Path) -> str | None:
    """Return a child registry id for a path under base, or None if skipped."""
    return (
        None
        if path.name in {"parsers"} or path.name.startswith(".")
        else (
            (None if path.stem == base.name else f"{prefix}-{path.stem}")
            if path.is_file() and path.suffix == ".yaml"
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
    """Collect registry ids from YAML files under one root (skip parsers/)."""
    return (
        [
            yaml_path_to_id(path, root)
            for path in sorted(root.rglob("*.yaml"))
            if "parsers" not in path.relative_to(root).parts
        ]
        if root.is_dir()
        else []
    )
