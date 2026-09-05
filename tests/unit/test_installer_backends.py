"""Tests for installer backend protocol dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.backends import PathBackend
from maina.core.errors import InstallError, UnsupportedPlatformError
from maina.core.protocols import InstallerBackend
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


class MockBackend(InstallerBackend):
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


def test_register_backends_rejects_a_backend_missing_the_protocol(tmp_path: Path) -> None:
    """A custom backend that doesn't subclass InstallerBackend fails at
    registration, with a clear message — not an AttributeError the first
    time a manifest dispatches to it."""
    from maina.core.errors import RegisterError

    class NotABackend:
        @property
        def name(self) -> str:
            return "broken"

    with pytest.raises(RegisterError, match="is not an InstallerBackend subclass"):
        Installer(
            cache_root=tmp_path / "cache",
            language=FakeLanguage(tmp_path / "cache"),
            registry=Registry([]),
            backends=[NotABackend()],  # type: ignore
        )


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


def test_resolve_rejects_unknown_backend_before_provisioning(tmp_path: Path) -> None:
    ensured: list[tuple[str, str]] = []

    @dataclass
    class SpyLanguage:
        cache_root: Path

        def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
            ensured.append((runtime, version))
            return self.cache_root

        def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
            del runtime, version
            return {"PATH": str(self.cache_root)}

    root = tmp_path / "reg"
    write_yaml(root / "base.yaml", {"abstract": True, "requires": {"node": "22"}})
    write_yaml(root / "t.yaml", {"cli": "t", "install": {"backend": "nope"}})
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=SpyLanguage(tmp_path / "cache"),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    with pytest.raises(InstallError, match="unknown install backend"):
        installer.resolve(Registry(root).load("t"))
    assert ensured == []


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
    assert installer.prepare_ids(None) == (["child"], {})
    assert installer.prepare_ids(["child"]) == (["child"], {})


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
        satisfied, missing, _failures = installer.check_ids()
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


def test_clean_ids_removes_cached_installs(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    root = tmp_path / "reg"
    write_yaml(root / "a.yaml", {"cli": "aaa", "version": "1", "install": {"backend": "path"}})
    write_yaml(root / "b.yaml", {"cli": "bbb", "version": "1", "install": {"backend": "path"}})
    (cache / "tools" / "aaa" / "1").mkdir(parents=True)
    (cache / "tools" / "bbb" / "1").mkdir(parents=True)
    installer = Installer(
        cache_root=cache,
        language=FakeLanguage(cache),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    # A named id deletes only its own <cli>/<version>, and prunes the emptied cli dir.
    assert installer.clean_ids(["a"]) == ["aaa/1"]
    assert not (cache / "tools" / "aaa").exists()
    assert (cache / "tools" / "bbb" / "1").exists()
    assert installer.clean_ids() == ["bbb"]  # no ids -> whole tree, per cli
    assert installer.clean_ids() == []


def test_clean_ids_keeps_a_sibling_version(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    root = tmp_path / "reg"
    write_yaml(root / "v1.yaml", {"cli": "tool", "version": "1", "install": {"backend": "path"}})
    write_yaml(root / "v2.yaml", {"cli": "tool", "version": "2", "install": {"backend": "path"}})
    (cache / "tools" / "tool" / "1").mkdir(parents=True)
    (cache / "tools" / "tool" / "2").mkdir(parents=True)
    installer = Installer(
        cache_root=cache,
        language=FakeLanguage(cache),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    assert installer.clean_ids(["v1"]) == ["tool/1"]
    assert not (cache / "tools" / "tool" / "1").exists()
    assert (cache / "tools" / "tool" / "2").exists()  # sibling version untouched


def test_clean_ids_rejects_abstract_id(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "base.yaml", {"abstract": True})
    write_yaml(
        root / "leaf.yaml",
        {"cli": "leaf", "extends": "base", "install": {"backend": "path"}},
    )
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    with pytest.raises(InstallError, match="cannot clean 'base'"):
        installer.clean_ids(["base"])


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
    satisfied, missing, _failures = installer.check_ids()
    assert satisfied == ["here"]
    assert missing == ["gone"]
    # is_satisfied on an abstract manifest is a no-op "satisfied".
    write_yaml(root / "base.yaml", {"cli": "base", "abstract": True})
    assert installer.is_satisfied(Registry(root).load("base")) is True


def test_check_ids_honours_missing_runtime(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "needsnode.yaml",
        {"cli": "true", "install": {"backend": "path"}, "requires": {"node": "22"}},
    )
    registry = Registry(root)

    class NoRuntimeLanguage(FakeLanguage):
        def is_present(self, runtime: str, version: str) -> bool:
            del runtime, version
            return False

    installer = Installer(
        cache_root=tmp_path / "cache",
        language=NoRuntimeLanguage(tmp_path / "cache"),
        registry=registry,
        backends=[PathBackend()],
    )
    satisfied, missing, _failures = installer.check_ids()
    assert missing == ["needsnode"]
    assert satisfied == []


def test_check_ids_unknown_backend_errors(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "x.yaml", {"cli": "x", "install": {"backend": "mystery"}})
    registry = Registry(root)
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=registry,
        backends=[PathBackend()],
    )
    # An unknown backend is a manifest bug, not a "missing" install.
    with pytest.raises(InstallError, match="unknown install backend"):
        installer.is_satisfied(registry.load("x"))


def test_check_ids_is_batch_resilient_to_a_bad_backend(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(root / "bad.yaml", {"cli": "x", "install": {"backend": "mystery"}})
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry(root),
        backends=[PathBackend()],
    )
    satisfied, missing, failures = installer.check_ids()
    assert satisfied == ["good"]
    assert missing == []
    assert "bad" in failures
    assert "unknown install backend" in failures["bad"]


def test_check_ids_is_batch_resilient_to_a_probe_raising_oserror(tmp_path: Path) -> None:
    """A probe raising a raw OSError (permission denied, a broken symlink
    loop) must land in failures like any other bad id — not escape check_ids
    and blank the report for every other, healthy id."""

    class FlakyBackend(InstallerBackend):
        @property
        def name(self) -> str:
            return "flaky"

        def resolve(self, *_a: object, **_k: object) -> tuple[str, dict[str, str]]:
            raise NotImplementedError

        @staticmethod
        def probe(*_a: object, **_k: object) -> bool:
            raise OSError("permission denied")

    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "true", "install": {"backend": "path"}})
    write_yaml(root / "flaky.yaml", {"cli": "x", "install": {"backend": "flaky"}})
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry(root),
        backends=[PathBackend(), FlakyBackend()],
    )
    satisfied, missing, failures = installer.check_ids()
    assert satisfied == ["good"]
    assert missing == []
    assert "permission denied" in failures["flaky"]


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
    assert installer.prepare_ids(None) == ([], {})
    # Explicit prepare_ids collects it as a failure rather than aborting.
    prepared, failures = installer.prepare_ids(["maconly"])
    assert prepared == []
    assert list(failures) == ["maconly"]
