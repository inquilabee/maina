"""Tests for installer backend protocol dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import yaml

from pyporter.backends import PathBackend
from pyporter.errors import InstallError
from pyporter.installer import Installer
from pyporter.registry import Manifest, Registry
from pyporter.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path

    from pyporter.protocols import InstallContext, LanguageRuntimeProvider


@dataclass
class FakeLanguage:
    """Minimal LanguageRuntimeProvider for unit tests."""

    cache_root: Path

    def ensure(self, runtime: str, version: str) -> Path:
        del runtime, version
        return self.cache_root

    def env(self, runtime: str, version: str) -> dict[str, str]:
        del runtime, version
        return {"PATH": str(self.cache_root)}


class MockBackend:
    """InstallerBackend that records resolve calls."""

    def __init__(self, name: str = "mock") -> None:
        self._name = name
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        del version, ctx
        self.calls.append(manifest.id)
        return "/bin/true", env


def load_backend_manifest(tmp_path: Path, *, backend: str, tool_id: str = "demo") -> Manifest:
    root = tmp_path / "reg"
    root.mkdir(exist_ok=True)
    (root / f"{tool_id}.yaml").write_text(
        yaml.dump({"cli": tool_id, "install": {"backend": backend}}),
        encoding="utf-8",
    )
    return Registry(root).load(tool_id)


def test_custom_installer_backend(tmp_path: Path) -> None:
    mock = MockBackend()
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry([]),
        backends=[mock],
    )
    exe, _env = installer.resolve(load_backend_manifest(tmp_path, backend="mock"))
    assert exe == "/bin/true"
    assert mock.calls == ["demo"]


def test_installer_backend_replace(tmp_path: Path) -> None:
    first = MockBackend("path")
    second = MockBackend("path")
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        backends=[first, second],
    )
    installer.resolve(load_backend_manifest(tmp_path, backend="path"))
    assert first.calls == []
    assert second.calls == ["demo"]


def test_unknown_backend_errors(tmp_path: Path) -> None:
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        backends=[PathBackend()],
    )
    with pytest.raises(InstallError, match="unknown install backend"):
        installer.resolve(load_backend_manifest(tmp_path, backend="missing"))


def test_runtime_register_custom_backend(tmp_path: Path) -> None:
    mock = MockBackend()
    root = tmp_path / "tools"
    root.mkdir()
    (root / "x.yaml").write_text(
        yaml.dump({"cli": "x", "install": {"backend": "mock"}}),
        encoding="utf-8",
    )
    rt = Runtime(cache_root=tmp_path / "cache", language=FakeLanguage(tmp_path / "cache"))
    rt.register(registry_dirs=[root], installers=[mock])
    exe, _env = rt.installer.resolve(rt.registry.load("x"))
    assert exe == "/bin/true"
    assert mock.calls == ["x"]


def test_fake_language_satisfies_protocol(tmp_path: Path) -> None:
    lang: LanguageRuntimeProvider = FakeLanguage(tmp_path)
    assert lang.ensure("node", "20") == tmp_path
