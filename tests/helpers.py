"""Shared test helpers (imported by tests; also re-exported from conftest)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import yaml

from maina.core.registry import Registry
from maina.runtime import Runtime, register

# Example manifests used only by the test suite. maina itself ships no catalog.
FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry"

# One throwaway cache root for the whole run, so a preferred() Runtime in a test
# never reads $MAINA_CACHE or touches the developer's real ~/.cache/maina.
FIXTURE_CACHE_ROOT = Path(tempfile.mkdtemp(prefix="maina-test-cache-"))


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    """Write *data* as YAML to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def fixture_registry() -> Registry:
    """A ``Registry`` rooted at the bundled test fixture manifests."""
    return Registry([FIXTURE_REGISTRY])


def fixture_runtime(**kwargs: Any) -> Runtime:
    """A preferred ``Runtime`` with the fixture manifests registered.

    Hermetic: ``use_env=False`` so an ambient ``$MAINA_REGISTRY`` cannot overlay
    (and shadow) a fixture id, and a throwaway ``cache_root`` so the suite never
    writes the developer's real cache.
    """
    if kwargs:
        rt = Runtime(**kwargs)
    else:
        rt = Runtime.preferred(use_env=False, cache_root=FIXTURE_CACHE_ROOT)
    register(rt, registry_dirs=[FIXTURE_REGISTRY])
    return rt
