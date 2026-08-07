"""Shared pytest fixtures for pyporter tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import pyporter
from pyporter.runtime import Runtime

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def restore_preferred_runtime() -> Iterator[None]:
    """Keep the active composition Runtime at preferred defaults between tests."""
    pyporter.use(Runtime.preferred())
    yield
    pyporter.use(Runtime.preferred())
