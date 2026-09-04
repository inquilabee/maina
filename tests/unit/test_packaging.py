"""Packaging smoke: maina ships the engine only, no tool catalog."""

from __future__ import annotations

from importlib.resources import files

import pytest

from helpers import fixture_runtime
from maina.runtime import Runtime


def test_package_ships_no_registry() -> None:
    assert not files("maina").joinpath("registry").is_dir()


def test_preferred_has_empty_catalog() -> None:
    assert Runtime.preferred(use_env=False).registry.iter_ids() == []


def test_caller_catalog_loads_a_manifest() -> None:
    manifest = fixture_runtime().registry.load("jq")
    assert manifest.cli == "jq"
    assert manifest.install.backend == "github"


def test_worked_example_catalog_is_valid() -> None:
    import pathlib

    import maina

    catalog = pathlib.Path(__file__).resolve().parents[2] / "examples" / "catalog"
    rt = maina.Runtime.preferred(use_env=False, registry_dirs=[catalog])
    ids = rt.registry.list_commands(None)
    assert {"hello", "jq", "shellcheck", "prettier", "jscpd", "git-remote-add"} <= set(ids)
    for tid in ids:
        maina.Tool(tid, runtime=rt)  # every manifest loads + passes the parser check
    assert maina.Tool("jscpd", runtime=rt).manifest.output.from_file == "{output}/jscpd-report.json"
    assert maina.Tool("git-remote-add", runtime=rt).manifest.cli == "git"


@pytest.mark.integration
def test_integration_marker_registered() -> None:
    """Placeholder so the integration marker is exercised without network I/O."""
    assert fixture_runtime().registry.load("shellcheck").cli == "shellcheck"
