"""Shared test helpers (imported by tests; also re-exported from conftest)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

import yaml

from maina.core.registry import Registry
from maina.runtime import Runtime, register

# Example manifests used only by the test suite. maina itself ships no catalog.
FIXTURE_REGISTRY = Path(__file__).parent / "fixtures" / "registry"

# One throwaway cache root for the whole run, so a preferred() Runtime in a test
# never reads $MAINA_CACHE or touches the developer's real ~/.cache/maina.
FIXTURE_CACHE_ROOT = Path(tempfile.mkdtemp(prefix="maina-test-cache-"))


def write_yaml(path: Path, data: dict[str, Any]) -> Path:
    """Write *data* as YAML to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def fixture_registry() -> Registry:
    """A ``Registry`` rooted at the bundled test fixture manifests."""
    return Registry([FIXTURE_REGISTRY])


def fixture_runtime(**kwargs: Any) -> Runtime:
    """A preferred ``Runtime`` with the fixture manifests registered.

    Hermetic: ``use_env=False`` so an ambient ``$MAINA_REGISTRY`` cannot overlay
    (and shadow) a fixture id, and a throwaway ``cache_root`` so the suite never
    writes the developer's real cache.
    """
    if kwargs:
        rt = Runtime(**kwargs)
    else:
        rt = Runtime.preferred(use_env=False, cache_root=FIXTURE_CACHE_ROOT)
    register(rt, registry_dirs=[FIXTURE_REGISTRY])
    return rt


class FakePopen:
    """Stand-in for :class:`subprocess.Popen`, matching what
    ``maina.core.subprocess_utils.run_capped`` needs from it: readable
    ``.stdout`` / ``.stderr``, a writable ``.stdin``, and ``.wait()`` /
    ``.kill()``. Construct via :func:`fake_popen_factory` so a test's argv
    inspection (``argv[0].endswith("curl")`` etc.) still works.
    """

    def __init__(
        self,
        argv: list[str],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        wait_raises: BaseException | None = None,
    ) -> None:
        self.args = argv
        self.stdout: io.BytesIO | None = io.BytesIO(stdout)
        self.stderr: io.BytesIO | None = io.BytesIO(stderr)
        self.stdin: io.BytesIO | None = io.BytesIO()
        self.pid = 0  # never a real group: kill_tree falls back to kill()
        self.returncode = returncode
        self._wait_raises = wait_raises
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self._wait_raises is not None:
            raise self._wait_raises
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def fake_popen_factory(
    respond: Any,
) -> Any:
    """Build a ``subprocess.Popen`` replacement from *respond*.

    *respond* is called with the same positional/keyword args a real
    ``Popen(argv, ...)`` call would get, and must return either a
    :class:`FakePopen` or a plain ``dict`` of :class:`FakePopen` kwargs
    (``stdout=``, ``stderr=``, ``returncode=``, ``wait_raises=``).
    """

    def popen(argv: list[str], **kwargs: Any) -> FakePopen:
        result = respond(argv, **kwargs)
        return result if isinstance(result, FakePopen) else FakePopen(argv, **result)

    return popen


def patch_popen(monkeypatch: Any, target: str, respond: Any) -> None:
    """``monkeypatch.setattr(target, fake_popen_factory(respond))`` shorthand.

    *target* is a dotted ``subprocess.Popen`` reference, e.g.
    ``"maina.core.subprocess_utils.subprocess.Popen"``.
    """
    monkeypatch.setattr(target, fake_popen_factory(respond))
