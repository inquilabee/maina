"""Shared subprocess-wrap and cache-containment helpers for install backends.

The same two shapes were duplicated across ``pip.py``, ``npm.py``, and
``mise.py``: wrap a ``check=True`` subprocess call so ``TimeoutExpired`` /
``CalledProcessError`` / ``OSError`` become one domain error instead of a raw
stdlib exception, and assert a resolved cache path stays under its own root.
Pure transforms with no accumulating state — plain functions, not a class.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import IO

DEFAULT_CHUNK_SIZE = 64 * 1024
# Cap on stdout/stderr collected from an install-phase subprocess (npm/pip
# install, mise install/which/exec, the mise.run bootstrap script) — these run
# less-trusted code than the tool itself (allow_scripts / allow_sdist already
# document install-time code as untrusted), so they get the same order-of-
# magnitude bound as the tool-run boundary (runner.RUN_OUTPUT_MAX_BYTES).
INSTALL_OUTPUT_MAX_BYTES = 64 * 1024 * 1024
# How long to wait for a drain thread after the process tree was killed. A
# grandchild that left the session (setsid) can still hold the pipe open.
DRAIN_JOIN_TIMEOUT = 2.0


class OutputCapExceededError(Exception):
    """Internal signal: stdout/stderr crossed *max_bytes*. Callers translate this."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__(f"stdout/stderr exceeded the {max_bytes} byte cap")
        self.max_bytes = max_bytes


def run_capped(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: str | None = None,
    timeout: float | None,
    stdin: bytes | None = None,
    max_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> subprocess.CompletedProcess[bytes]:
    """Run *argv* via ``Popen`` with stdout/stderr each capped at *max_bytes*.

    Mirrors ``subprocess.run(capture_output=True)`` but never buffers past the
    cap: two daemon threads drain stdout/stderr independently (reading both
    from the main thread would deadlock once one pipe's OS buffer fills while
    the other still has unread data). Crossing the cap kills the process and
    raises :class:`OutputCapExceededError` instead of silently truncating and
    returning a partial result. ``subprocess.TimeoutExpired`` propagates
    (after the process tree is killed and the drain threads joined), matching
    ``subprocess.run``'s own contract.

    The child runs in its own session so a timeout, overflow, or interrupt can
    kill its descendants too — killing only the direct child leaves a forking
    tool's grandchildren holding the pipes open until they exit on their own.
    A consequence: the tool has no controlling terminal, so it cannot prompt
    through ``/dev/tty``. A child still holding the pipes after the tool exits
    is killed after a short grace period rather than waited on.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    out_buf, err_buf = bytearray(), bytearray()
    overflowed = threading.Event()
    out_thread = start_drain_thread(proc, proc.stdout, out_buf, overflowed, max_bytes, chunk_size)
    err_thread = start_drain_thread(proc, proc.stderr, err_buf, overflowed, max_bytes, chunk_size)
    try:
        write_stdin(proc, stdin)
        returncode = proc.wait(timeout=timeout)
    except BaseException:
        # Timeout, Ctrl-C, or a stdin write failure: never leave the tree running.
        kill_tree(proc)
        proc.wait()
        join_drains(proc, out_thread, err_thread)
        raise
    join_drains(proc, out_thread, err_thread)
    if overflowed.is_set():
        raise OutputCapExceededError(max_bytes)
    return subprocess.CompletedProcess(argv, returncode, bytes(out_buf), bytes(err_buf))


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


def run_or_wrap(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    error_cls: type[Exception],
    on_timeout: Callable[[], str],
    on_failure: Callable[[str], str],
) -> subprocess.CompletedProcess[str]:
    """Run *argv* (``check=True``), wrapping timeout / failure / OS errors.

    *on_timeout* builds the message for a ``TimeoutExpired``; *on_failure*
    receives the failure detail (stderr, or the exception text for an
    ``OSError``) and builds the message for that case. Both raise *error_cls*.
    """
    try:
        return subprocess.run(
            argv, check=True, env=env, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise error_cls(on_timeout()) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise error_cls(on_failure(detail)) from exc
    except OSError as exc:
        raise error_cls(on_failure(str(exc))) from exc


def run_or_wrap_capped(
    argv: list[str],
    *,
    env: dict[str, str],
    timeout: float,
    max_bytes: int = INSTALL_OUTPUT_MAX_BYTES,
    stdin: bytes | None = None,
    error_cls: type[Exception],
    on_timeout: Callable[[], str],
    on_failure: Callable[[str], str],
) -> subprocess.CompletedProcess[str]:
    """Like :func:`run_or_wrap`, but stdout/stderr are each bounded at
    *max_bytes* (see :func:`run_capped`) instead of ``subprocess.run``'s own
    unbounded ``capture_output=True``.

    *on_timeout* builds the message for a ``TimeoutExpired``; *on_failure*
    receives the failure detail (stderr, or the exception text for an
    ``OSError`` / output-cap overrun) and builds the message for that case.
    Both raise *error_cls*, matching ``run_or_wrap``'s ``check=True`` contract
    (a non-zero exit also raises *error_cls* via *on_failure*).
    """
    try:
        completed = run_capped(argv, env=env, timeout=timeout, stdin=stdin, max_bytes=max_bytes)
    except subprocess.TimeoutExpired as exc:
        raise error_cls(on_timeout()) from exc
    except OutputCapExceededError as exc:
        raise error_cls(on_failure(f"output exceeded the {exc.max_bytes} byte cap")) from exc
    except OSError as exc:
        raise error_cls(on_failure(str(exc))) from exc
    stdout = completed.stdout.decode("utf-8", "replace")
    stderr = completed.stderr.decode("utf-8", "replace")
    if completed.returncode != 0:
        detail = (stderr or stdout).strip() or f"exited {completed.returncode}"
        raise error_cls(on_failure(detail))
    return subprocess.CompletedProcess(argv, completed.returncode, stdout, stderr)


def resolve_within(path: Path, root: Path, *, error_cls: type[Exception], message: str) -> Path:
    """Resolve *path* and assert it stays under *root*; else raise *error_cls*."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise error_cls(message)
    return resolved
