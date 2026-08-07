"""Registry discovery, inheritance merge, and hyphen-id resolution."""

from __future__ import annotations

from .ids import join_tool_id, normalize_id, yaml_path_to_id
from .loader import MAP_KEYS, Registry
from .specs import (
    ARG_KEYS,
    BASE_TOOL_ID,
    IGNORED_MANIFEST_KEYS,
    INSTALL_KEYS,
    LEGACY_REQUIRES_KEYS,
    MANIFEST_PREFIX,
    OUTPUT_KEYS,
    REQUIRES_KEYS,
    ArgSpec,
    InstallSpec,
    Manifest,
    OutputSpec,
    RequiresSpec,
    optional_str,
    parse_args_map,
    unknown_keys,
)

__all__ = [
    "ARG_KEYS",
    "BASE_TOOL_ID",
    "IGNORED_MANIFEST_KEYS",
    "INSTALL_KEYS",
    "LEGACY_REQUIRES_KEYS",
    "MANIFEST_PREFIX",
    "MAP_KEYS",
    "OUTPUT_KEYS",
    "REQUIRES_KEYS",
    "ArgSpec",
    "InstallSpec",
    "Manifest",
    "OutputSpec",
    "Registry",
    "RequiresSpec",
    "join_tool_id",
    "normalize_id",
    "optional_str",
    "parse_args_map",
    "unknown_keys",
    "yaml_path_to_id",
]
