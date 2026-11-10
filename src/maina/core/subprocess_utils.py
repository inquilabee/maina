"""Shared subprocess-wrap and cache-containment helpers for install backends.

The same two shapes were duplicated across ``pip.py``, ``npm.py``, and
``mise.py``: wrap a ``check=True`` subprocess call so ``TimeoutExpired`` /
``CalledProcessError`` / ``OSError`` become one domain error instead of a raw
stdlib exception, and assert a resolved cache path stays under its own root.
Pure transforms with no accumulating state — plain functions, not a class.
"""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING

from maina.core.procgroup import join_drains, kill_tree, start_drain_thread, write_stdin

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEFAULT_CHUNK_SIZE = 64 * 1024
# Cap on stdout/stderr collected from an install-phase subprocess (npm/pip
# install, mise install/which/exec, the mise.run bootstrap script) — these run
# less-trusted code than the tool itself (allow_scripts / allow_sdist already
# document install-time code as untrusted), so they get the same order-of-
# magnitude bound as the tool-run boundary (runner.RUN_OUTPUT_MAX_BYTES).
INSTALL_OUTPUT_MAX_BYTES = 64 * 1024 * 1024


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
