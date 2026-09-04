"""Registry discovery, inheritance merge, and hyphen-id resolution."""

from __future__ import annotations

from .common import MANIFEST_PREFIX, manifest_error, optional_str, scalar_str, unknown_keys
from .ids import join_tool_id, normalize_id, yaml_path_to_id
from .loader import MAP_KEYS, Registry
from .requires import RequiresSpec
from .specs import (
    ARG_KEYS,
    BASE_TOOL_ID,
    IGNORED_MANIFEST_KEYS,
    INSTALL_KEYS,
    OUTPUT_KEYS,
    ArgSpec,
    InstallSpec,
    Manifest,
    OutputSpec,
    parse_args_map,
)

__all__ = [
    "ARG_KEYS",
    "BASE_TOOL_ID",
    "IGNORED_MANIFEST_KEYS",
    "INSTALL_KEYS",
    "MANIFEST_PREFIX",
    "MAP_KEYS",
    "OUTPUT_KEYS",
    "ArgSpec",
    "InstallSpec",
    "Manifest",
    "OutputSpec",
    "Registry",
    "RequiresSpec",
    "join_tool_id",
    "manifest_error",
    "normalize_id",
    "optional_str",
    "parse_args_map",
    "scalar_str",
    "unknown_keys",
    "yaml_path_to_id",
]
