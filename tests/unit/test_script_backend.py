"""Tests for the script installer backend (install.backend: script)."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.backends import ScriptBackend
from maina.core.errors import InstallError
from maina.core.protocols import InstallContext
from maina.core.registry import Manifest, Registry
from maina.installer import Installer
from maina.runner import ToolRunner

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class NoLanguage:
    """Script tools never provision a language runtime through this provider —
    ``requires:`` pins are composed into ``env`` before ``resolve()`` runs."""

    def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
        del runtime, version
        raise AssertionError("script backend must not need a language provider")

    def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
        del runtime, version
        raise AssertionError("script backend must not need a language provider")


def install_ctx(tmp_path: Path) -> InstallContext:
    return InstallContext(cache_root=tmp_path / "cache", language=NoLanguage())


def resolve_script(manifest: Manifest, tmp_path: Path) -> tuple[str, dict[str, str]]:
    return ScriptBackend().resolve(manifest, version="default", env={}, ctx=install_ctx(tmp_path))


def is_executable(path: Path) -> bool:
    return bool(path.stat().st_mode & stat.S_IXUSR)


def test_script_backend_execs_a_single_file_and_chmods_it(tmp_path: Path) -> None:
    entry = tmp_path / "deploy.py"
    entry.write_text("#!/usr/bin/env python3\nprint('deployed')\n", encoding="utf-8")
    manifest = Manifest(id="deploy", data={"cli": "deploy"}, entry_path=entry)
    exe, env = resolve_script(manifest, tmp_path)
    assert exe == str(entry)
    assert env == {}
    assert is_executable(entry)


def test_script_backend_rejects_missing_shebang(tmp_path: Path) -> None:
    entry = tmp_path / "deploy.py"
    entry.write_text("print('no shebang')\n", encoding="utf-8")
    manifest = Manifest(id="deploy", data={"cli": "deploy"}, entry_path=entry)
    with pytest.raises(InstallError, match="shebang"):
        resolve_script(manifest, tmp_path)


def test_script_backend_requires_entry_path(tmp_path: Path) -> None:
    manifest = Manifest(id="deploy", data={"cli": "deploy"}, entry_path=None)
    with pytest.raises(InstallError, match=r"install\.entry"):
        resolve_script(manifest, tmp_path)


def test_script_backend_execs_a_directory_via_its_matching_entry_file(tmp_path: Path) -> None:
    pkg = tmp_path / "release"
    pkg.mkdir()
    (pkg / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
    entry_file = pkg / "release.py"
    entry_file.write_text("#!/usr/bin/env python3\nprint('releasing')\n", encoding="utf-8")
    manifest = Manifest(id="release", data={"cli": "release"}, entry_path=pkg)
    exe, _env = resolve_script(manifest, tmp_path)
    assert exe == str(entry_file)
    assert is_executable(entry_file)


def test_script_backend_rejects_dir_with_no_matching_entry(tmp_path: Path) -> None:
    pkg = tmp_path / "release"
    pkg.mkdir()
    (pkg / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = Manifest(id="release", data={"cli": "release"}, entry_path=pkg)
    with pytest.raises(InstallError, match="exactly one file"):
        resolve_script(manifest, tmp_path)


def test_script_backend_rejects_dir_with_ambiguous_entries(tmp_path: Path) -> None:
    pkg = tmp_path / "release"
    pkg.mkdir()
    (pkg / "release.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (pkg / "release.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    manifest = Manifest(id="release", data={"cli": "release"}, entry_path=pkg)
    with pytest.raises(InstallError, match="exactly one file"):
        resolve_script(manifest, tmp_path)


def test_script_backend_probe(tmp_path: Path) -> None:
    entry = tmp_path / "deploy.py"
    entry.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    ctx = install_ctx(tmp_path)
    present = Manifest(id="deploy", data={"cli": "deploy"}, entry_path=entry)
    missing = Manifest(id="deploy", data={"cli": "deploy"}, entry_path=None)
    assert ScriptBackend.probe(present, version="default", ctx=ctx) is True
    assert ScriptBackend.probe(missing, version="default", ctx=ctx) is False


def script_installer(tmp_path: Path, registry: Registry) -> Installer:
    return Installer(
        cache_root=tmp_path / "cache",
        language=NoLanguage(),
        registry=registry,
        backends=[ScriptBackend()],
    )


def test_script_backend_end_to_end_single_file(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    (root / "scripts").mkdir(parents=True)
    entry = root / "scripts" / "greet.py"
    entry.write_text(
        "#!/usr/bin/env python3\nimport sys\nprint(f'hi {sys.argv[1]}')\n", encoding="utf-8"
    )
    write_yaml(
        root / "greet.yaml",
        {
            "cli": "greet",
            "install": {"backend": "script", "entry": "greet.py"},
            "args": {"name": {"positional": True, "required": True}},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(registry.load("greet"), installer=script_installer(tmp_path, registry))
    result = runner.run({"name": "maina"})
    assert result.ok
    assert "hi maina" in result.raw


def test_script_backend_end_to_end_directory_imports_a_sibling(tmp_path: Path) -> None:
    """The whole point of a directory entry: the entry file can import a
    sibling module that lives next to it in the same scripts/ package."""
    root = tmp_path / "reg"
    pkg = root / "scripts" / "release"
    pkg.mkdir(parents=True)
    (pkg / "helper.py").write_text("GREETING = 'shipped'\n", encoding="utf-8")
    (pkg / "release.py").write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
        "import helper\n"
        "print(helper.GREETING)\n",
        encoding="utf-8",
    )
    write_yaml(
        root / "release.yaml",
        {
            "cli": "release",
            "install": {"backend": "script", "entry": "release"},
            "output": {"success_codes": [0]},
        },
    )
    registry = Registry(root)
    runner = ToolRunner(registry.load("release"), installer=script_installer(tmp_path, registry))
    result = runner.run({})
    assert result.ok
    assert "shipped" in result.raw


def test_non_script_backend_ignores_a_stray_install_entry(tmp_path: Path) -> None:
    """install.entry only triggers eager resolution under backend: script — a
    stray entry: under another backend must not fail load()."""
    root = tmp_path / "reg"
    write_yaml(
        root / "t.yaml",
        {"cli": "echo", "install": {"backend": "path", "entry": "does/not/exist.py"}},
    )
    manifest = Registry(root).load("t")
    assert manifest.entry_path is None
