"""The published manifest JSON Schema must track the loader's key sets."""

from __future__ import annotations

import json
from pathlib import Path

from maina.core.registry import build_manifest_schema
from maina.core.registry.jsonschema import BUILTIN_BACKENDS
from maina.core.registry.specs import ARG_KEYS, INSTALL_KEYS, OUTPUT_KEYS, TOP_LEVEL_KEYS
from maina.runtime import Runtime

SCHEMA_FILE = Path(__file__).parents[2] / "docs" / "maina-manifest.schema.json"


def test_checked_in_schema_matches_the_generator() -> None:
    on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert on_disk == build_manifest_schema(), (
        "docs/maina-manifest.schema.json is stale — regenerate with `maina schema`"
    )


def test_schema_covers_every_loader_key() -> None:
    schema = build_manifest_schema()
    assert set(schema["properties"]) == set(TOP_LEVEL_KEYS)
    assert set(schema["$defs"]["arg"]["properties"]) == set(ARG_KEYS)
    assert set(schema["$defs"]["install"]["properties"]) == set(INSTALL_KEYS)
    assert set(schema["$defs"]["output"]["properties"]) == set(OUTPUT_KEYS)


def test_schema_blocks_are_closed() -> None:
    schema = build_manifest_schema()
    assert schema["additionalProperties"] is False
    for name in ("arg", "install", "output"):
        assert schema["$defs"][name]["additionalProperties"] is False


def test_schema_accepts_the_null_drop_idiom_everywhere_the_loader_does() -> None:
    """`<key>: null` down `extends` drops an inherited args / env / assets /
    sha256 entry (loader.py); the schema must not red-flag the docs' own
    examples of that idiom."""
    schema = build_manifest_schema()
    assert schema["properties"]["args"]["additionalProperties"] == {
        "anyOf": [{"$ref": "#/$defs/arg"}, {"type": "null"}]
    }
    assert "null" in schema["properties"]["env"]["additionalProperties"]["type"]
    install = schema["$defs"]["install"]["properties"]
    assert "null" in install["assets"]["additionalProperties"]["type"]
    sha256_any_of = install["sha256"]["additionalProperties"]["anyOf"]
    assert {"type": "null"} in sha256_any_of


def test_schema_choices_items_allow_the_types_scalar_str_accepts() -> None:
    schema = build_manifest_schema()
    choices = schema["$defs"]["arg"]["properties"]["choices"]
    assert set(choices["items"]["type"]) == {"string", "integer"}


def test_builtin_backend_hint_matches_the_shipped_backends() -> None:
    registered = Runtime.preferred().installer.backend_names
    assert set(BUILTIN_BACKENDS) == set(registered)
    schema = build_manifest_schema()
    backend_schema = schema["$defs"]["install"]["properties"]["backend"]
    # An open string (a caller can register a custom backend), not an enum —
    # the builtins are only a hint.
    assert backend_schema["type"] == "string"
    assert backend_schema["examples"] == list(BUILTIN_BACKENDS)
