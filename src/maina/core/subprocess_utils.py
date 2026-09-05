"""Shared subprocess-wrap and cache-containment helpers for install backends.

The same two shapes were duplicated across ``pip.py``, ``npm.py``, and
``mise.py``: wrap a ``check=True`` subprocess call so ``TimeoutExpired`` /
``CalledProcessError`` / ``OSError`` become one domain error instead of a raw
stdlib exception, and assert a resolved cache path stays under its own root.
Pure transforms with no accumulating state — plain functions, not a class.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import IO

DEFAULT_CHUNK_SIZE = 64 * 1024


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
    (after the process is killed and both drain threads joined), matching
    ``subprocess.run``'s own contract.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        cwd=cwd,
        env=env,
    )
    out_buf, err_buf = bytearray(), bytearray()
    overflowed = threading.Event()
    out_thread = start_drain_thread(proc, proc.stdout, out_buf, overflowed, max_bytes, chunk_size)
    err_thread = start_drain_thread(proc, proc.stderr, err_buf, overflowed, max_bytes, chunk_size)
    try:
        write_stdin(proc, stdin)
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        out_thread.join()
        err_thread.join()
        raise
    out_thread.join()
    err_thread.join()
    if overflowed.is_set():
        raise OutputCapExceededError(max_bytes)
    return subprocess.CompletedProcess(argv, returncode, bytes(out_buf), bytes(err_buf))


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
    """Read *pipe* into *buf*; kill *proc* once the combined cap is crossed."""
    if pipe is None:
        return
    # pipe torn down under us after kill()
    with contextlib.suppress(OSError, ValueError):
        while chunk := pipe.read(chunk_size):
            if overflowed.is_set():
                continue
            if len(buf) + len(chunk) > max_bytes:
                overflowed.set()
                proc.kill()
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


def resolve_within(path: Path, root: Path, *, error_cls: type[Exception], message: str) -> Path:
    """Resolve *path* and assert it stays under *root*; else raise *error_cls*."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise error_cls(message)
    return resolved
