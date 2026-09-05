"""Tests for the npm installer backend — split out of test_installer_backends.py
to stay under this repo's module-size gate, mirroring test_pip_backend.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from maina.core.errors import InstallError

if TYPE_CHECKING:
    from pathlib import Path


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


def test_npm_install_timeout_is_wrapped_as_install_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from helpers import patch_popen
    from maina.backends.npm import NpmBackend

    def respond(argv: list[str], **_kw: object) -> dict[str, object]:
        return {"wait_raises": subprocess.TimeoutExpired(cmd=argv, timeout=600)}

    patch_popen(monkeypatch, "maina.core.subprocess_utils.subprocess.Popen", respond)
    with pytest.raises(InstallError, match="timed out after"):
        NpmBackend._npm_install("/usr/bin/npm", tmp_path, "tool@1", {"PATH": "/usr/bin"})


def test_npm_install_uses_ignore_scripts_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from helpers import patch_popen
    from maina.backends.npm import NpmBackend

    calls: list[list[str]] = []

    def respond(argv: list[str], **_kw: object) -> dict[str, object]:
        calls.append(list(argv))
        return {"returncode": 0}

    patch_popen(monkeypatch, "maina.core.subprocess_utils.subprocess.Popen", respond)
    NpmBackend._npm_install("/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"})
    assert "--ignore-scripts" in calls[0]
    calls.clear()
    NpmBackend._npm_install(
        "/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"}, allow_scripts=True
    )
    assert "--ignore-scripts" not in calls[0]


def test_npm_install_separates_the_package_spec_with_dashdash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defense in depth matching pip.py: the package spec follows a `--`, so
    it can never be read as a flag even though install.package is already
    validated as a safe npm name."""
    from helpers import patch_popen
    from maina.backends.npm import NpmBackend

    calls: list[list[str]] = []

    def respond(argv: list[str], **_kw: object) -> dict[str, object]:
        calls.append(list(argv))
        return {"returncode": 0}

    patch_popen(monkeypatch, "maina.core.subprocess_utils.subprocess.Popen", respond)
    NpmBackend._npm_install("/usr/bin/npm", tmp_path / "x", "jscpd@1", {"PATH": "/usr/bin"})
    assert calls[0][-2:] == ["--", "jscpd@1"]


def test_npm_install_output_over_cap_raises_install_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flooding (or malicious) npm install script must not buffer unbounded
    into this process — it should be capped the same way tool execution is."""
    import maina.backends.npm as npm_module
    from helpers import patch_popen
    from maina.backends.npm import NpmBackend

    monkeypatch.setattr(npm_module, "INSTALL_OUTPUT_MAX_BYTES", 10)

    def respond(_argv: list[str], **_kw: object) -> dict[str, object]:
        return {"returncode": 0, "stdout": b"x" * 1000}

    patch_popen(monkeypatch, "maina.core.subprocess_utils.subprocess.Popen", respond)
    with pytest.raises(InstallError, match="byte cap"):
        NpmBackend._npm_install("/usr/bin/npm", tmp_path, "tool@1", {"PATH": "/usr/bin"})
