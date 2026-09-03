"""Packaging smoke: maina ships the engine only, no tool catalog."""

from __future__ import annotations

from importlib.resources import files

import pytest

from helpers import fixture_runtime
from maina.runtime import Runtime


def test_package_ships_no_registry() -> None:
    assert not files("maina").joinpath("registry").is_dir()


def test_preferred_has_empty_catalog() -> None:
    assert Runtime.preferred().registry.iter_ids() == []


def test_caller_catalog_loads_a_manifest() -> None:
    manifest = fixture_runtime().registry.load("jq")
    assert manifest.cli == "jq"
    assert manifest.install.backend == "github"


@pytest.mark.integration
def test_integration_marker_registered() -> None:
    """Placeholder so the integration marker is exercised without network I/O."""
    assert fixture_runtime().registry.load("shellcheck").cli == "shellcheck"
