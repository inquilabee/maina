"""Security and install-reliability tests for Agent B changes."""

from __future__ import annotations

import io
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from helpers import write_yaml
from maina.backends.archive import ArchiveFetcher
from maina.backends.npm import NpmBackend
from maina.core.errors import InstallError
from maina.core.registry import InstallSpec, Manifest, Registry, RequiresSpec
from maina.installer import Installer
from maina.runner import ToolRunner

if TYPE_CHECKING:
    from maina.core.protocols import InstallContext


@dataclass
class FakeLanguage:
    cache_root: Path

    def ensure(self, runtime: str, version: str) -> Path:
        del version
        return self.cache_root / runtime

    def env(self, runtime: str, version: str) -> dict[str, str]:
        del version
        return {"PATH": f"/opt/{runtime}/bin:/usr/bin", "MARK": "shared"}


class RecordingFetcher(ArchiveFetcher):
    """ArchiveFetcher stub that records download_and_extract calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Path, str, str]] = []

    def download_and_extract(
        self,
        url: str,
        dest_dir: Path,
        *,
        binary: str,
        asset_name: str,
    ) -> None:
        self.calls.append((url, dest_dir, binary, asset_name))
        dest_dir.mkdir(parents=True, exist_ok=True)
        exe = dest_dir / binary
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)


class RecordingBackend:
    def __init__(self) -> None:
        self.seen_env: dict[str, str] | None = None

    @property
    def name(self) -> str:
        return "mock"

    def resolve(
        self,
        manifest: Manifest,
        *,
        version: str,
        env: dict[str, str],
        ctx: InstallContext,
    ) -> tuple[str, dict[str, str]]:
        del manifest, version, ctx
        self.seen_env = env
        env["MUTATED"] = "1"
        env["PATH"] = f"/tool/bin:{env.get('PATH', '')}"
        return "/bin/true", env


def test_assert_safe_member_rejects_prefix_sibling(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    (tmp_path / "destevil").mkdir()
    # Classic startswith false positive: .../destevil starts with .../dest.
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.assert_safe_member(dest, "../destevil/bin")


def test_assert_safe_member_rejects_dotdot(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.assert_safe_member(dest, "../outside")


def test_assert_safe_member_allows_nested(tmp_path: Path) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    ArchiveFetcher.assert_safe_member(dest, "bin/tool")


def test_safe_extract_tar_rejects_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "evil.tar.gz"
    dest = tmp_path / "out"
    dest.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape")
        payload = b"x"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    with pytest.raises(InstallError, match="unsafe path"):
        ArchiveFetcher.safe_extract_tar(archive, dest, mode="r:gz")


def test_fetch_https_streams_with_size_cap(tmp_path: Path) -> None:
    fetcher = ArchiveFetcher(max_bytes=8)
    dest = tmp_path / "asset.bin"
    response = MagicMock()
    response.headers = {}
    response.raise_for_status = MagicMock()
    response.iter_content = MagicMock(return_value=[b"12345", b"67890"])
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    fetcher._session = session
    with pytest.raises(InstallError, match="size cap"):
        fetcher.fetch_https("https://example.com/a.bin", dest)


def test_fetch_https_rejects_non_https(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="non-https"):
        ArchiveFetcher().fetch_https("http://example.com/a.bin", tmp_path / "x")


def test_download_and_extract_happy_path_mock_https(tmp_path: Path) -> None:
    """Mock HTTPS download; extract a tar.gz that contains the named binary."""
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w:gz") as tar:
        payload = b"#!/bin/sh\necho ok\n"
        info = tarfile.TarInfo(name="bin/demo")
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
    body = archive_bytes.getvalue()

    fetcher = ArchiveFetcher()
    response = MagicMock()
    response.headers = {"Content-Length": str(len(body))}
    response.raise_for_status = MagicMock()
    response.iter_content = MagicMock(return_value=[body])
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=response)
    fetcher._session = session

    dest = tmp_path / "out"
    fetcher.download_and_extract(
        "https://example.com/releases/demo.tar.gz",
        dest,
        binary="demo",
        asset_name="demo.tar.gz",
    )
    exe = dest / "demo"
    assert exe.is_file()
    assert exe.read_bytes().startswith(b"#!/bin/sh")
    session.get.assert_called_once()
    assert session.get.call_args.args[0].startswith("https://")


def test_github_backend_resolve_uses_fetcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maina.backends.github import GithubBackend
    from maina.core.protocols import InstallContext

    monkeypatch.setattr("maina.backends.github.platform_key", lambda: "linux-x86_64")
    fetcher = RecordingFetcher()

    manifest = Manifest(
        id="demo",
        data={"cli": "demo", "version": "1.0.0"},
        install=InstallSpec(
            backend="github",
            repo="acme/demo",
            assets={"linux-x86_64": "demo-linux.tar.gz"},
            binary="demo",
            tag="v1.0.0",
        ),
    )
    lang = FakeLanguage(tmp_path / "cache")
    ctx = InstallContext(cache_root=tmp_path / "cache", language=lang)
    backend = GithubBackend(fetcher=fetcher)
    exe, env = backend.resolve(manifest, version="1.0.0", env={"PATH": "/usr/bin"}, ctx=ctx)
    assert Path(exe).name == "demo"
    assert Path(exe).is_file()
    assert str(Path(exe).parent) in env["PATH"]
    assert fetcher.calls == [
        (
            "https://github.com/acme/demo/releases/download/v1.0.0/demo-linux.tar.gz",
            ctx.tool_bin_dir("demo", "1.0.0"),
            "demo",
            "demo-linux.tar.gz",
        )
    ]


def test_installer_copies_env_before_backend_mutation(tmp_path: Path) -> None:
    backend = RecordingBackend()
    lang = FakeLanguage(tmp_path / "cache")
    shared = lang.env("node", "20")
    installer = Installer(
        cache_root=tmp_path / "cache",
        language=lang,
        registry=Registry([]),
        backends=[backend],
    )
    root = tmp_path / "reg"
    write_yaml(
        root / "demo.yaml",
        {
            "cli": "demo",
            "install": {"backend": "mock"},
            "requires": {"runtime": "node", "version": "20"},
        },
    )
    manifest = Registry(root).load("demo")
    _exe, returned = installer.resolve(manifest)
    assert returned.get("MUTATED") == "1"
    assert "MUTATED" not in shared
    assert backend.seen_env is not shared


def test_install_dir_lock_exclusive(tmp_path: Path) -> None:
    dest = tmp_path / "tools" / "x" / "1"
    with Installer.install_dir_lock(dest):
        assert (dest / ".porter-install.lock").is_file()


def test_toolrunner_timeout_maps_to_install_error(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "sleepy.yaml",
        {
            "cli": "true",
            "install": {"backend": "path"},
            "timeout": 0.01,
            "output": {"success_codes": [0]},
        },
    )
    manifest = Registry(root).load("sleepy")

    class Pathish:
        @property
        def name(self) -> str:
            return "path"

        def resolve(
            self,
            manifest: Manifest,
            *,
            version: str,
            env: dict[str, str],
            ctx: InstallContext,
        ) -> tuple[str, dict[str, str]]:
            del manifest, version, ctx
            return "/bin/true", env

    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry([]),
        backends=[Pathish()],
    )

    def boom(*_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["/bin/true"], timeout=0.01)

    runner = ToolRunner(manifest, installer=installer, executor=boom)
    with pytest.raises(InstallError, match="timed out"):
        runner.run({})


def test_toolrunner_executor_smoke(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "output": {"success_codes": [0]}},
    )
    manifest = Registry(root).load("echo")

    class Pathish:
        @property
        def name(self) -> str:
            return "path"

        def resolve(
            self,
            manifest: Manifest,
            *,
            version: str,
            env: dict[str, str],
            ctx: InstallContext,
        ) -> tuple[str, dict[str, str]]:
            del manifest, version, ctx
            return "echo", env

    installer = Installer(
        cache_root=tmp_path / "cache",
        language=FakeLanguage(tmp_path / "cache"),
        registry=Registry([]),
        backends=[Pathish()],
    )

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="hi\n", stderr="")

    result = ToolRunner(manifest, installer=installer, executor=fake_run).run({})
    assert result.ok
    assert result.raw == "hi\n"
    assert result.command[0] == "echo"


def test_npm_node_check_uses_which(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []
    ensured: list[tuple[str, str]] = []

    def fake_which(cmd: str, path: str | None = None) -> str | None:
        calls.append((cmd, path))
        # PATH contains the substring "node" via node_modules; which must still find a real node.
        return "/usr/bin/node" if cmd == "node" else None

    @dataclass
    class TrackingLanguage:
        cache_root: Path

        def ensure(self, runtime: str, version: str) -> Path:
            ensured.append((runtime, version))
            return self.cache_root

        def env(self, runtime: str, version: str) -> dict[str, str]:
            del runtime, version
            return {"PATH": str(self.cache_root)}

    monkeypatch.setattr("maina.backends.npm.shutil.which", fake_which)
    ctx = MagicMock()
    ctx.language = TrackingLanguage(tmp_path)
    env = {"PATH": "/opt/node_modules/bin:/usr/bin"}
    manifest = Manifest(
        id="x",
        data={"cli": "x"},
        requires=RequiresSpec(runtimes={"node": "20"}),
    )
    NpmBackend._ensure_node_on_path(manifest, env, ctx)
    assert calls == [("node", env["PATH"])]
    assert ensured == []


def test_mise_ensure_skips_install_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from maina.mise import MiseRuntime

    rt = MiseRuntime(cache_root=tmp_path)
    monkeypatch.setattr(rt, "mise_bin", lambda: Path("/fake/mise"))
    which_calls: list[str] = []

    def fake_which(
        _mise: object,
        spec: str,
        _runtime: object,
        _env: object,
    ) -> Path:
        which_calls.append(spec)
        return tmp_path / "bin"

    monkeypatch.setattr(MiseRuntime, "_mise_which", staticmethod(fake_which))

    def fail_run(*_a: object, **_k: object) -> None:
        raise AssertionError("mise install should be skipped")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert rt.ensure("node", "20") == tmp_path / "bin"
    assert which_calls == ["node@20"]
