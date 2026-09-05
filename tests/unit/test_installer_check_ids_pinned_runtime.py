"""check_ids' PATH-composition-parity test — split out of
test_installer_backends.py to stay under this repo's module-size gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from helpers import write_yaml
from maina.backends import PathBackend
from maina.core.registry import Registry
from maina.installer import Installer

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class FakeLanguage:
    """Minimal LanguageRuntimeProvider for unit tests."""

    cache_root: Path

    def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
        del runtime, version
        return self.cache_root

    def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
        del runtime, version
        return {"PATH": str(self.cache_root)}


def test_check_searches_the_path_a_pinned_runtime_would_compose(tmp_path: Path) -> None:
    """A ``path`` tool provided by a ``requires:`` runtime must not read as
    ``missing`` in a check that ``run`` would pass — ``resolve`` searches the
    composed PATH, so the probe has to as well. Still side-effect free: the bin
    dir comes from a lookup that installs nothing.
    """
    runtime_bin = tmp_path / "node-bin"
    runtime_bin.mkdir()
    exe = runtime_bin / "only-in-the-runtime"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)

    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {
            "cli": "only-in-the-runtime",
            "install": {"backend": "path"},
            "requires": {"node": "22"},
        },
    )

    installs: list[tuple[str, str]] = []

    class InstalledLanguage(FakeLanguage):
        def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
            installs.append((runtime, version))
            return runtime_bin

        def present_bin_dir(self, runtime: str, version: str) -> Path | None:
            del runtime, version
            return runtime_bin

    installer = Installer(
        cache_root=tmp_path / "cache",
        language=InstalledLanguage(tmp_path / "cache"),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    satisfied, missing, failures = installer.check_ids()
    assert satisfied == ["t"], (missing, failures)
    assert installs == []  # nothing was provisioned by the check
