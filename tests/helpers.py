"""Shared test helpers (imported by tests; also re-exported from conftest)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    """Write *data* as YAML to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path
