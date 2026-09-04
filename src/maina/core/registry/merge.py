"""Inheritance merge rules for an ``extends`` chain.

One layer at a time, child onto parent. The rules are small but load-bearing:
which blocks deep-merge, and where a ``null`` means "drop what the parent said"
rather than "set this to nothing".
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Blocks that merge per key rather than being replaced wholesale by the child.
MAP_KEYS = frozenset({"args", "install", "env", "output", "requires"})


class ManifestMerge:
    """Deep-merge one manifest layer onto its parent."""

    # install sub-maps that deep-merge per platform key (a child that overrides
    # one platform's asset must not drop the others).
    INSTALL_DEEP_KEYS = ("assets", "sha256")

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
                out[key] = ManifestMerge.merge_requires(out.get(key), value)
                continue
            if key in MAP_KEYS and isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = ManifestMerge.merge_map_key(key, out[key], value)
                continue
            out[key] = deepcopy(value)
        return out

    @classmethod
    def merge_map_key(
        cls, key: str, parent: dict[str, Any], child: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge a MAP_KEYS block. ``args`` merges per-arg-name then per-field;
        ``env`` merges per-key with ``<var>: null`` dropping an inherited one
        (mirrors ``requires``); ``install`` shallow-merges but deep-merges
        ``assets`` / ``sha256`` the same way."""
        out = deepcopy(parent)
        if key == "args":
            return cls.merge_args(out, child)
        if key == "env":
            return cls.merge_null_dropping(parent, child)
        out |= deepcopy(child)
        deep_subs = cls.INSTALL_DEEP_KEYS if key == "install" else ()
        for sub in deep_subs:
            if isinstance(parent.get(sub), dict) and isinstance(child.get(sub), dict):
                out[sub] = cls.merge_null_dropping(parent[sub], child[sub])
        return out

    @staticmethod
    def merge_null_dropping(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """``{**parent, **child}``, except a ``null`` value in the child drops
        that inherited key — the null-drop idiom ``args`` and ``requires`` use,
        applied to ``env`` and to ``install``'s ``assets`` / ``sha256`` maps (so
        a version-bump child can shed a parent's stale asset name or digest, and
        a child can turn off an inherited env var)."""
        merged = deepcopy(parent)
        for key, value in child.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = deepcopy(value)
        return merged

    @staticmethod
    def merge_args(out: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
        """Merge the ``args`` block per arg name, then per field within an arg."""
        for arg_name, child_spec in child.items():
            if child_spec is None:
                out.pop(arg_name, None)
                continue
            mergeable = isinstance(child_spec, dict) and isinstance(out.get(arg_name), dict)
            out[arg_name] = {**out[arg_name], **child_spec} if mergeable else deepcopy(child_spec)
        return out

    @staticmethod
    def merge_requires(parent: Any, child: Any) -> Any:
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
