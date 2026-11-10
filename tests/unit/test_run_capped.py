"""run_capped must not outlive a killed tool's own children."""

from __future__ import annotations

import subprocess
import time

import pytest

from maina.core.subprocess_utils import OutputCapExceededError, run_capped


def seconds_until_killed(script: str, *, timeout: float | None, max_bytes: int) -> float:
    started = time.monotonic()
    with pytest.raises((subprocess.TimeoutExpired, OutputCapExceededError)):
        run_capped(
            ["sh", "-c", script],
            env={"PATH": "/usr/bin:/bin"},
            timeout=timeout,
            max_bytes=max_bytes,
        )
    return time.monotonic() - started


def test_timeout_returns_promptly_when_the_tool_forks() -> None:
    # `sleep` is a grandchild that keeps the stdout pipe open after `sh` is killed.
    assert seconds_until_killed("sleep 30 & wait", timeout=1, max_bytes=1024) < 10


def test_output_overflow_returns_promptly_when_the_tool_forks() -> None:
    assert seconds_until_killed("sleep 30 & yes", timeout=None, max_bytes=1024) < 10


def test_a_child_left_holding_the_pipes_after_the_tool_exits_is_not_waited_on() -> None:
    started = time.monotonic()
    done = run_capped(
        ["sh", "-c", "sleep 30 & echo hi"],
        env={"PATH": "/usr/bin:/bin"},
        timeout=1,
        max_bytes=1024,
    )
    assert (done.stdout, done.returncode) == (b"hi\n", 0)
    assert time.monotonic() - started < 10
