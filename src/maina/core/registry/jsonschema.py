"""Project a manifest's *shape* into JSON Schema (Draft 2020-12).

The loader (:mod:`maina.core.registry.specs` / ``loader.py``) is the authority
on what a manifest may contain. This module turns the key sets it validates
against — ``TOP_LEVEL_KEYS``, ``ARG_KEYS``, ``INSTALL_KEYS``, ``OUTPUT_KEYS`` —
into a schema an editor can check against before ``Tool(id)`` ever runs.

It is a shape check only. Cross-field rules (``separator`` xor ``args.order``,
a ``from_file`` template var must also be an arg, which ``install`` keys a given
backend needs) stay in the loader; a passing schema does not mean a loadable
manifest.
"""

from __future__ import annotations

from typing import Any

from maina.core.platform import KNOWN_PLATFORM_KEYS

from .specs import (
    ARG_KEYS,
    ARG_TYPES,
    CURRENT_SCHEMA,
    INSTALL_KEYS,
    JOIN_VALUES,
    OUTPUT_KEYS,
    TOP_LEVEL_KEYS,
    VALID_PLATFORMS,
)

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID = (
    "https://raw.githubusercontent.com/inquilabee/maina/main/docs/maina-manifest.schema.json"
)

# Built-in ``install.backend`` names. A test cross-checks this against
# ``Runtime.preferred().installer.backend_names`` so a new shipped backend
# forces an update here.
BUILTIN_BACKENDS = ("github", "npm", "path")

PLATFORM_MAP: dict[str, Any] = {
    "type": "object",
    "propertyNames": {"enum": sorted(KNOWN_PLATFORM_KEYS)},
    "additionalProperties": {"type": "string"},
}

TOP_LEVEL_PROPERTIES: dict[str, dict[str, Any]] = {
    "schema": {"const": CURRENT_SCHEMA},
    "cli": {"type": "string", "minLength": 1},
    "version": {"type": "string"},
    "subcommand": {"type": "string"},
    "extends": {"type": "string"},
    "abstract": {"type": "boolean"},
    "platforms": {"type": "array", "items": {"enum": sorted(VALID_PLATFORMS)}},
    "timeout": {"type": "number", "exclusiveMinimum": 0},
    "separator": {"type": "string", "minLength": 1},
    "parser": {"type": "string", "minLength": 1},
    "env": {"type": "object", "additionalProperties": {"type": "string"}},
    "args": {"type": "object", "additionalProperties": {"$ref": "#/$defs/arg"}},
    "install": {"$ref": "#/$defs/install"},
    "requires": {"type": ["object", "null"], "additionalProperties": {"type": ["string", "null"]}},
    "output": {"$ref": "#/$defs/output"},
    "name": {"type": "string"},
}

ARG_PROPERTIES: dict[str, dict[str, Any]] = {
    "type": {"enum": sorted(ARG_TYPES)},
    "flag": {"type": "string", "minLength": 1},
    "joiner": {"type": "string"},
    "positional": {"type": "boolean"},
    "required": {"type": "boolean"},
    "repeat": {"type": "boolean"},
    "join": {"enum": sorted(JOIN_VALUES)},
    "order": {"type": "integer"},
    "default": {},
    "description": {"type": "string"},
    "help": {"type": "string"},
}

INSTALL_PROPERTIES: dict[str, dict[str, Any]] = {
    "backend": {"enum": list(BUILTIN_BACKENDS)},
    "repo": {"type": "string", "minLength": 1},
    "assets": PLATFORM_MAP,
    "binary": {"type": "string", "minLength": 1},
    "tag": {"type": "string", "minLength": 1},
    "package": {"type": "string", "minLength": 1},
    "sha256": {
        "type": "object",
        "propertyNames": {"enum": sorted(KNOWN_PLATFORM_KEYS)},
        "additionalProperties": {"type": "string", "pattern": "^[0-9a-fA-F]{64}$"},
    },
    "allow_scripts": {"type": "boolean"},
    "allow_any_executable": {"type": "boolean"},
}

OUTPUT_PROPERTIES: dict[str, dict[str, Any]] = {
    "format": {"type": "string"},
    "success_codes": {"type": "array", "items": {"type": "integer"}},
    "from_file": {"type": "string", "minLength": 1},
}


def object_block(keys: frozenset[str], properties: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A closed object schema whose properties are exactly *keys* (sorted)."""
    if missing := sorted(k for k in keys if k not in properties):
        raise ValueError(f"no JSON-schema shape for keys: {', '.join(missing)}")
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: properties[name] for name in sorted(keys)},
    }


def build_manifest_schema() -> dict[str, Any]:
    """Build the manifest JSON Schema from the loader's key sets."""
    top = object_block(TOP_LEVEL_KEYS, TOP_LEVEL_PROPERTIES)
    return {
        "$schema": SCHEMA_DIALECT,
        "$id": SCHEMA_ID,
        "title": "maina tool manifest",
        "description": (
            "Shape of a maina YAML manifest. Cross-field and backend-specific "
            "rules are still enforced by the loader at Tool() time."
        ),
        **top,
        "$defs": {
            "arg": object_block(ARG_KEYS, ARG_PROPERTIES),
            "install": object_block(INSTALL_KEYS, INSTALL_PROPERTIES),
            "output": object_block(OUTPUT_KEYS, OUTPUT_PROPERTIES),
        },
    }
