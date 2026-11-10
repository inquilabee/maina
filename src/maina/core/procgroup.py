"""Child-process plumbing for ``run_capped``: kill a whole tree, drain pipes with a cap."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from typing import IO

# How long to wait for a drain thread after the process tree was killed. A
# grandchild that left the session (setsid) can still hold the pipe open.
DRAIN_JOIN_TIMEOUT = 2.0


def join_drains(proc: subprocess.Popen[bytes], *threads: threading.Thread) -> None:
    """Wait for the output pipes to close; kill a child still holding them after a grace period."""
    for thread in threads:
        thread.join(DRAIN_JOIN_TIMEOUT)
    if any(thread.is_alive() for thread in threads):
        kill_tree(proc, members_alive=True)
        for thread in threads:
            thread.join(DRAIN_JOIN_TIMEOUT)


def kill_tree(proc: subprocess.Popen[bytes], *, members_alive: bool = False) -> None:
    """SIGKILL *proc* and its whole session (it leads one, see :func:`run_capped`).

    The group id is only trusted while the leader is unreaped or *members_alive*
    (a live drain thread proves a member still holds the pipe); otherwise the pid
    could in theory have been reused.
    """
    # pid > 0: killpg(0) would signal our own process group.
    if hasattr(os, "killpg") and proc.pid > 0 and (proc.returncode is None or members_alive):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
            return
    proc.kill()


def start_drain_thread(
    proc: subprocess.Popen[bytes],
    pipe: IO[bytes] | None,
    buf: bytearray,
    overflowed: threading.Event,
    max_bytes: int,
    chunk_size: int,
) -> threading.Thread:
    """Start a daemon thread draining *pipe* into *buf*, capped at *max_bytes*."""
    thread = threading.Thread(
        target=drain_capped, args=(proc, pipe, buf, overflowed, max_bytes, chunk_size), daemon=True
    )
    thread.start()
    return thread


def drain_capped(
    proc: subprocess.Popen[bytes],
    pipe: IO[bytes] | None,
    buf: bytearray,
    overflowed: threading.Event,
    max_bytes: int,
    chunk_size: int,
) -> None:
    """Read *pipe* into *buf*; kill *proc*'s tree once the combined cap is crossed."""
    if pipe is None:
        return
    # pipe torn down under us after kill()
    with contextlib.suppress(OSError, ValueError):
        while chunk := pipe.read(chunk_size):
            if overflowed.is_set():
                continue
            if len(buf) + len(chunk) > max_bytes:
                overflowed.set()
                kill_tree(proc)
                continue
            buf.extend(chunk)


def write_stdin(proc: subprocess.Popen[bytes], stdin: bytes | None) -> None:
    """Write *stdin* to *proc* and close the pipe; ignore a peer that already exited."""
    if stdin is None or proc.stdin is None:
        return
    with contextlib.suppress(BrokenPipeError, OSError):
        proc.stdin.write(stdin)
        proc.stdin.close()
