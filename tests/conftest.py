"""Shared pytest fixtures and helpers for pyporter tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import pyporter
from helpers import write_yaml
from pyporter.runtime import SESSION, Runtime

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["restore_session_runtime", "write_yaml"]


@pytest.fixture(autouse=True)
def restore_session_runtime() -> Iterator[None]:
    """Isolate each test on preferred Runtime; restore the prior session snapshot.

    Snapshots the previous SESSION Runtime so teardown does not rebuild
    ``Runtime.preferred()`` a second time when a prior instance exists.
    """
    previous = SESSION.current
    pyporter.use(Runtime.preferred())
    yield
    if previous is not None:
        SESSION.set(previous)
    else:
        pyporter.use(Runtime.preferred())
