"""Packaging smoke: shipped registry YAML is loadable from the installed package."""

from __future__ import annotations

from importlib.resources import files

import pytest

from maina.runtime import Runtime


def test_shipped_registry_yaml_via_importlib_resources() -> None:
    jq_yaml = files("maina").joinpath("registry", "jq.yaml")
    assert jq_yaml.is_file()
    text = jq_yaml.read_text(encoding="utf-8")
    assert "cli: jq" in text


def test_preferred_loads_shipped_manifest() -> None:
    manifest = Runtime.preferred().registry.load("jq")
    assert manifest.cli == "jq"
    assert manifest.install.backend == "github"


@pytest.mark.integration
def test_integration_marker_registered() -> None:
    """Placeholder so the integration marker is exercised without network I/O."""
    assert Runtime.preferred().registry.load("shellcheck").cli == "shellcheck"
