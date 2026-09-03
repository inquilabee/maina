"""Shared pytest fixtures and helpers for maina tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import maina
from helpers import fixture_runtime, write_yaml
from maina.runtime import SESSION

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["fixture_runtime", "restore_session_runtime", "write_yaml"]


@pytest.fixture(autouse=True)
def restore_session_runtime() -> Iterator[None]:
    """Isolate each test on a fixture-catalog Runtime; restore the prior session.

    maina ships no catalog, so the session default here is a preferred Runtime
    with the test fixture manifests registered.
    """
    previous = SESSION.current
    maina.use(fixture_runtime())
    yield
    if previous is not None:
        SESSION.set(previous)
    else:
        maina.use(fixture_runtime())
