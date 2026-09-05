"""Unit tests for the pip install backend (no network — see tests/integration)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from maina.backends import PipBackend
from maina.core.errors import InstallError
from maina.core.protocols import InstallContext
from maina.core.registry import InstallSpec, Manifest, Registry, RequiresSpec
from maina.installer import Installer


class FakeLanguage:
    """LanguageRuntimeProvider double that records what it provisioned."""

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.ensured: list[tuple[str, str]] = []

    def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
        self.ensured.append((runtime, version))
        return self.bin_dir

    def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
        del runtime, version
        return {"PATH": str(self.bin_dir)}


def pip_manifest(cli: str = "toolx", **install: Any) -> Manifest:
    spec = InstallSpec(backend="pip", **install)
    return Manifest(id=cli, data={"cli": cli, "version": "1.2.3"}, install=spec)


def context(tmp_path: Path) -> InstallContext:
    return InstallContext(cache_root=tmp_path / "cache", language=FakeLanguage(tmp_path / "nope"))


@pytest.fixture
def recorded_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture every subprocess argv and fake a working venv + entry point."""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(list(argv))
        if "venv" in argv:  # `<python> -m venv <dir>` — lay down what venv would
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("", encoding="utf-8")
        if "install" in argv:  # pip install — lay down the console script
            venv = Path(argv[0]).parent.parent
            (venv / "bin" / "toolx").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_pip_install_refuses_source_distributions_by_default(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    """Building an sdist runs the package's own setup.py with the composed
    environment — the same exposure npm lifecycle scripts carry, which maina
    already refuses unless a manifest opts in."""
    PipBackend().resolve(pip_manifest(), version="1.2.3", env={}, ctx=context(tmp_path))
    install = next(argv for argv in recorded_runs if "install" in argv)
    assert "--only-binary=:all:" in install
    assert install[-1] == "toolx==1.2.3"


def test_pip_install_allows_sdists_only_when_the_manifest_opts_in(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    PipBackend().resolve(
        pip_manifest(allow_sdist=True), version="1.2.3", env={}, ctx=context(tmp_path)
    )
    install = next(argv for argv in recorded_runs if "install" in argv)
    assert "--only-binary=:all:" not in install


def test_pip_install_never_takes_an_index_from_the_manifest(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    """A manifest must not be able to redirect where a package comes from."""
    PipBackend().resolve(pip_manifest(), version="1.2.3", env={}, ctx=context(tmp_path))
    install = next(argv for argv in recorded_runs if "install" in argv)
    assert not [flag for flag in install if "index" in flag or "extra-url" in flag]


def test_pip_installs_the_distribution_under_its_own_console_script_name(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    """``cli:`` is the script to run; ``install.package:`` is what to install."""
    manifest = Manifest(
        id="toolx",
        data={"cli": "toolx", "version": "1.2.3"},
        install=InstallSpec(backend="pip", package="toolx-dist"),
    )
    PipBackend().resolve(manifest, version="1.2.3", env={}, ctx=context(tmp_path))
    install = next(argv for argv in recorded_runs if "install" in argv)
    assert install[-1] == "toolx-dist==1.2.3"


def test_pip_backend_needs_a_version(tmp_path: Path) -> None:
    """An unpinned lint tool changes its findings under you."""
    manifest = Manifest(id="toolx", data={"cli": "toolx"}, install=InstallSpec(backend="pip"))
    with pytest.raises(InstallError, match="needs a version"):
        PipBackend().resolve(manifest, version="default", env={}, ctx=context(tmp_path))


def test_pip_builds_the_venv_from_the_host_interpreter_when_nothing_is_pinned(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    """Pinning is opt-in, so a catalog that does not care provisions nothing."""
    language = FakeLanguage(tmp_path / "nope")
    ctx = InstallContext(cache_root=tmp_path / "cache", language=language)
    PipBackend().resolve(pip_manifest(), version="1.2.3", env={}, ctx=ctx)
    venv_call = next(argv for argv in recorded_runs if "venv" in argv)
    assert venv_call[0] == sys.executable
    assert language.ensured == []


def test_a_python_pin_is_provisioned_like_any_other_runtime(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    """``requires: {python: "3.13"}`` puts one tool on its own interpreter
    without a shared environment for the rest of the catalog to negotiate."""
    pinned_bin = tmp_path / "py313"
    pinned_bin.mkdir()
    interpreter = pinned_bin / "python3"
    interpreter.write_text("", encoding="utf-8")
    interpreter.chmod(0o755)
    language = FakeLanguage(pinned_bin)
    ctx = InstallContext(cache_root=tmp_path / "cache", language=language)
    manifest = Manifest(
        id="toolx",
        data={"cli": "toolx", "version": "1.2.3"},
        install=InstallSpec(backend="pip"),
        requires=RequiresSpec(runtimes={"python": "3.13"}),
    )
    PipBackend().resolve(manifest, version="1.2.3", env={}, ctx=ctx)
    assert language.ensured == [("python", "3.13")]
    venv_call = next(argv for argv in recorded_runs if "venv" in argv)
    assert venv_call[0] == str(interpreter)


def test_each_tool_gets_its_own_venv_directory(
    tmp_path: Path, recorded_runs: list[list[str]]
) -> None:
    """Per-tool venvs, not one shared environment: two tools whose dependencies
    disagree cannot break each other."""
    del recorded_runs
    cache = tmp_path / "cache"
    first = PipBackend.entry_point(cache, "toolx", "1.2.3")
    second = PipBackend.entry_point(cache, "tooly", "4.5.6")
    assert first.parent.parent != second.parent.parent
    assert first.parent.parent.parent.parent.name == "toolx"


def test_a_python_pin_changes_the_cache_directory(tmp_path: Path) -> None:
    """Otherwise two tools pinned to different interpreters would share a venv."""
    installer = Installer(cache_root=tmp_path, registry=Registry([]))
    base = {"cli": "toolx", "version": "1.2.3"}
    plain = Manifest(id="toolx", data=base, install=InstallSpec(backend="pip"))
    pinned = Manifest(
        id="toolx",
        data=base,
        install=InstallSpec(backend="pip"),
        requires=RequiresSpec(runtimes={"python": "3.13"}),
    )
    assert installer._cache_version(plain) != installer._cache_version(pinned)


def test_probe_is_side_effect_free_and_reports_the_entry_point(tmp_path: Path) -> None:
    ctx = context(tmp_path)
    manifest = pip_manifest()
    assert PipBackend.probe(manifest, version="1.2.3", ctx=ctx) is False
    entry = PipBackend.entry_point(ctx.cache_root, "toolx", "1.2.3")
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    assert PipBackend.probe(manifest, version="1.2.3", ctx=ctx) is True


def test_an_install_that_produces_no_entry_point_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "venv" in argv:
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")  # never writes the script

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(InstallError, match="produced no 'toolx' entry point"):
        PipBackend().resolve(pip_manifest(), version="1.2.3", env={}, ctx=context(tmp_path))


def test_a_failed_pip_install_surfaces_pips_own_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "venv" in argv:
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "python").write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise subprocess.CalledProcessError(1, argv, "", "No matching distribution found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(InstallError, match="No matching distribution found"):
        PipBackend().resolve(pip_manifest(), version="1.2.3", env={}, ctx=context(tmp_path))
