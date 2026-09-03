"""Shared test helpers (imported by tests; also re-exported from conftest)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from maina.core.registry import Registry
from maina.runtime import Runtime, register

# Example manifests used only by the test suite. maina itself ships no catalog.
FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry"


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    """Write *data* as YAML to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def fixture_registry() -> Registry:
    """A ``Registry`` rooted at the bundled test fixture manifests."""
    return Registry([FIXTURE_REGISTRY])


def fixture_runtime(**kwargs: Any) -> Runtime:
    """A preferred ``Runtime`` with the fixture manifests registered."""
    rt = Runtime.preferred() if not kwargs else Runtime(**kwargs)
    register(rt, registry_dirs=[FIXTURE_REGISTRY])
    return rt
