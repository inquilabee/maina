"""Shared pytest fixtures and helpers for maina tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

import maina
from helpers import write_yaml
from maina.runtime import SESSION, Runtime

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
    maina.use(Runtime.preferred())
    yield
    if previous is not None:
        SESSION.set(previous)
    else:
        maina.use(Runtime.preferred())
