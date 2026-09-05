"""Shared subprocess-wrap and cache-containment helpers for install backends.

The same two shapes were duplicated across ``pip.py``, ``npm.py``, and
``mise.py``: wrap a ``check=True`` subprocess call so ``TimeoutExpired`` /
``CalledProcessError`` / ``OSError`` become one domain error instead of a raw
stdlib exception, and assert a resolved cache path stays under its own root.
Pure transforms with no accumulating state — plain functions, not a class.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


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
