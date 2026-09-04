"""Tests for installer backend protocol dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.backends import PathBackend
from maina.core.errors import InstallError, UnsupportedPlatformError
from maina.core.registry import Manifest, Registry
from maina.installer import Installer
from maina.runtime import Runtime, register

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.protocols import InstallContext, LanguageRuntimeProvider


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
    write_yaml(root / f"{tool_id}.yaml", {"cli": tool_id, "install": {"backend": backend}})
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
        registry=Registry([]),
        backends=[first, second],
    )
    installer.resolve(load_backend_manifest(tmp_path, backend="path"))
    assert first.calls == []
    assert second.calls == ["demo"]


def test_unknown_backend_errors(tmp_path: Path) -> None:
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry([]),
        backends=[PathBackend()],
    )
    with pytest.raises(InstallError, match="unknown install backend"):
        installer.resolve(load_backend_manifest(tmp_path, backend="missing"))


def test_runtime_register_custom_backend(tmp_path: Path) -> None:
    mock = MockBackend()
    root = tmp_path / "tools"
    write_yaml(root / "x.yaml", {"cli": "x", "install": {"backend": "mock"}})
    rt = Runtime(cache_root=tmp_path / "cache", language=FakeLanguage(tmp_path / "cache"))
    register(rt, registry_dirs=[root], installers=[mock])
    exe, _env = rt.installer.resolve(rt.registry.load("x"))
    assert exe == "/bin/true"
    assert mock.calls == ["x"]


def test_fake_language_satisfies_protocol(tmp_path: Path) -> None:
    lang: LanguageRuntimeProvider = FakeLanguage(tmp_path)
    assert lang.ensure("node", "20") == tmp_path


def test_installer_composes_multi_runtime_path(tmp_path: Path) -> None:
    ensured: list[tuple[str, str]] = []

    @dataclass
    class TrackingLanguage:
        cache_root: Path

        def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
            ensured.append((runtime, version))
            path = self.cache_root / "bins" / runtime / version / "bin"
            path.mkdir(parents=True, exist_ok=True)
            return path

        def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
            # Deliberately resets PATH — installer must not rely on env() loops.
            bin_dir = self.ensure(runtime, version)
            return {"PATH": str(bin_dir)}

    lang = TrackingLanguage(tmp_path / "cache")
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=lang,
        registry=Registry([]),
        backends=[MockBackend()],
    )
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"abstract": True, "requires": {"python": "3.14", "node": "latest"}},
    )
    write_yaml(
        root / "demo.yaml",
        {"cli": "demo", "install": {"backend": "mock"}},
    )
    manifest = Registry(root).load("demo")
    _exe, env = installer.resolve(manifest)
    assert ("python", "3.14") in ensured
    assert ("node", "latest") in ensured
    path = env["PATH"]
    assert str(tmp_path / "cache" / "bins" / "python" / "3.14" / "bin") in path
    assert str(tmp_path / "cache" / "bins" / "node" / "latest" / "bin") in path
    assert env["MISE_DATA_DIR"] == str(tmp_path / "cache" / "mise-data")


def test_npm_fallback_preserves_composed_python_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """npm ensure must prepend onto composed env, not replace PATH via env()."""
    from maina.backends.npm import NpmBackend
    from maina.core.protocols import InstallContext
    from maina.core.registry import Manifest, RequiresSpec

    python_bin = tmp_path / "cache" / "bins" / "python" / "3.14" / "bin"
    node_bin = tmp_path / "cache" / "bins" / "node" / "latest" / "bin"
    python_bin.mkdir(parents=True)
    node_bin.mkdir(parents=True)

    @dataclass
    class TrackingLanguage:
        cache_root: Path

        def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
            return self.cache_root / "bins" / runtime / version / "bin"

        def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
            # Simulates MiseRuntime.env: rebuilds PATH from a clean base.
            del runtime, version
            return {"PATH": "/usr/bin", "MISE_DATA_DIR": "wiped"}

    def fake_which(cmd: str, path: str | None = None) -> str | None:
        del path
        return None if cmd == "node" else "/usr/bin/true"

    monkeypatch.setattr("maina.backends.npm.shutil.which", fake_which)
    env = {"PATH": f"{python_bin}:/usr/bin", "OTHER": "keep"}
    lang = TrackingLanguage(tmp_path / "cache")
    ctx = InstallContext(cache_root=tmp_path / "cache", language=lang)
    manifest = Manifest(
        id="prettier",
        data={"cli": "prettier"},
        requires=RequiresSpec(runtimes={"python": "3.14"}),
    )
    NpmBackend._ensure_node_on_path(manifest, env, ctx)
    assert str(python_bin) in env["PATH"]
    assert str(node_bin) in env["PATH"]
    assert env["OTHER"] == "keep"
    assert env.get("MISE_DATA_DIR") != "wiped"


def test_prepare_ids_rejects_explicit_abstract(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "base.yaml",
        {"cli": "base", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "family" / "family.yaml",
        {"cli": "family", "abstract": True, "install": {"backend": "path"}},
    )
    write_yaml(
        root / "child.yaml",
        {
            "cli": "true",
            "extends": "base",
            "abstract": False,
            "install": {"backend": "path"},
        },
    )
    registry = Registry(root)
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=registry,
        backends=[PathBackend()],
    )
    with pytest.raises(InstallError, match="cannot prepare abstract"):
        installer.prepare_ids(["base"])
    with pytest.raises(InstallError, match="cannot prepare abstract"):
        installer.prepare_ids(["family"])
    prepared = installer.prepare_ids(None)
    assert prepared == ["child"]
    assert installer.prepare_ids(["child"]) == ["child"]


def test_prepare_ids_skips_broken_manifest_but_explicit_raises(tmp_path: Path) -> None:
    from maina.core.errors import ManifestError

    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "true", "install": {"backend": "path"}})
    (root / "broken.yaml").write_text("cli: broken\noutputs: {}\n", encoding="utf-8")
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    with pytest.warns(RuntimeWarning, match="failed to load"):
        satisfied, missing = installer.check_ids()
    assert "good" in satisfied + missing
    assert "broken" not in satisfied + missing
    with pytest.raises(ManifestError):
        installer.check_ids(["broken"])


def test_prepare_ids_rejects_catalog_abstract() -> None:
    from helpers import fixture_runtime

    rt = fixture_runtime()
    with pytest.raises(InstallError, match="cannot prepare abstract"):
        rt.installer.prepare_ids(["base"])
    with pytest.raises(InstallError, match="cannot prepare abstract"):
        rt.installer.prepare_ids(["kubectl"])


def test_check_ids_reports_satisfied_and_missing(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "here.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(root / "gone.yaml", {"cli": "no-such-binary-x", "install": {"backend": "path"}})
    registry = Registry(root)
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=registry,
        backends=[PathBackend()],
    )
    satisfied, missing = installer.check_ids()
    assert satisfied == ["here"]
    assert missing == ["gone"]
    # is_satisfied on an abstract manifest is a no-op "satisfied".
    write_yaml(root / "base.yaml", {"cli": "base", "abstract": True})
    assert installer.is_satisfied(Registry(root).load("base")) is True


def test_check_ids_unknown_backend_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "x.yaml", {"cli": "x", "install": {"backend": "mystery"}})
    registry = Registry(root)
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=registry,
        backends=[PathBackend()],
    )
    assert installer.is_satisfied(registry.load("x")) is False


def test_npm_install_rejects_out_of_cache_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maina.backends.npm import NpmBackend
    from maina.core.errors import InstallError as MainaInstallError

    outside = tmp_path / "global" / "bin" / "tool"
    outside.parent.mkdir(parents=True)
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    prefix = tmp_path / "cache" / "tools" / "tool" / "1" / "npm"
    link = prefix / "node_modules" / ".bin" / "tool"

    def fake_npm_install(
        npm: str, target: Path, pkg_spec: str, env: dict[str, str], **_kw: object
    ) -> None:
        del npm, pkg_spec, env
        link_root = target / "node_modules" / ".bin"
        link_root.mkdir(parents=True)
        (link_root / "tool").symlink_to(outside)

    monkeypatch.setattr(NpmBackend, "_npm_install", staticmethod(fake_npm_install))
    monkeypatch.setattr("maina.backends.npm.shutil.which", lambda *_a, **_k: "/usr/bin/npm")
    with pytest.raises(MainaInstallError, match="outside the tool cache"):
        NpmBackend._install_package("tool", "tool", "1", prefix, link, {"PATH": "/usr/bin"})


def test_npm_install_accepts_in_cache_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maina.backends.npm import NpmBackend

    prefix = tmp_path / "cache" / "tools" / "tool" / "1" / "npm"
    link = prefix / "node_modules" / ".bin" / "tool"

    def fake_npm_install(
        npm: str, target: Path, pkg_spec: str, env: dict[str, str], **_kw: object
    ) -> None:
        del npm, pkg_spec, env
        real = target / "node_modules" / "tool" / "bin" / "tool.js"
        real.parent.mkdir(parents=True)
        real.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        link.parent.mkdir(parents=True)
        link.symlink_to(real)

    monkeypatch.setattr(NpmBackend, "_npm_install", staticmethod(fake_npm_install))
    monkeypatch.setattr("maina.backends.npm.shutil.which", lambda *_a, **_k: "/usr/bin/npm")
    result = NpmBackend._install_package("tool", "tool", "1", prefix, link, {"PATH": "/usr/bin"})
    assert result == link
    assert (prefix / "package.json").is_file()


def test_resolve_rejects_unsupported_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("maina.installer.current_platform", lambda: "windows")
    root = tmp_path / "reg"
    write_yaml(
        root / "maconly.yaml",
        {"cli": "maconly", "platforms": ["macos", "linux"], "install": {"backend": "path"}},
    )
    registry = Registry(root)
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=registry,
        backends=[PathBackend()],
    )
    with pytest.raises(UnsupportedPlatformError, match="not supported on windows"):
        installer.resolve(registry.load("maconly"))
    # Implicit prepare_ids skips it instead of raising.
    assert installer.prepare_ids(None) == []
    # Explicit prepare_ids surfaces the error.
    with pytest.raises(UnsupportedPlatformError):
        installer.prepare_ids(["maconly"])
