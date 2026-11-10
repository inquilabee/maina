"""Security tests: archive extraction, HTTPS download + redirect allowlist, sha256, locking."""

from __future__ import annotations

import gzip
import io
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

from helpers import write_yaml
from maina.backends.archive import ArchiveFetcher
from maina.backends.npm import NpmBackend
from maina.core.errors import InstallError, MainaError, ToolTimeoutError
from maina.core.protocols import InstallerBackend
from maina.core.registry import InstallSpec, Manifest, Registry, RequiresSpec
from maina.installer import Installer
from maina.runner import ToolRunner

if TYPE_CHECKING:
    from maina.core.protocols import InstallContext


@dataclass
class FakeLanguage:
    cache_root: Path

    def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
        del version
        return self.cache_root / runtime

    def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
        del version
        return {"PATH": f"/opt/{runtime}/bin:/usr/bin", "MARK": "shared"}


class RecordingFetcher(ArchiveFetcher):
    """ArchiveFetcher stub that records download_and_extract calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Path, str, str]] = []
        self.sha256_seen: list[str | None] = []

    def download_and_extract(
        self,
        url: str,
        dest_dir: Path,
        *,
        binary: str,
        asset_name: str,
        sha256: str | None = None,
        allow_any_executable: bool = False,
    ) -> None:
        del allow_any_executable
        self.calls.append((url, dest_dir, binary, asset_name))
        self.sha256_seen.append(sha256)
        dest_dir.mkdir(parents=True, exist_ok=True)
        exe = dest_dir / binary
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)


class RecordingBackend(InstallerBackend):
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


def demo_targz(binary_name: str = "bin/demo") -> bytes:
    """A tiny gzipped tar containing one executable payload at *binary_name*.

    Built with a pinned gzip ``mtime=0``: ``tarfile.open(mode="w:gz")``
    otherwise stamps the gzip header with wall-clock time, and this helper is
    called twice per test (once to seed a mocked response, once in a
    byte-equality assertion) — a run that straddled a one-second boundary
    got two different byte strings for "the same" archive, a real,
    reproduced-in-CI flake.
    """
    archive_bytes = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=archive_bytes, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w|") as tar,
    ):
        payload = b"#!/bin/sh\necho ok\n"
        info = tarfile.TarInfo(name=binary_name)
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
    return archive_bytes.getvalue()


def body_response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.headers = {"Content-Length": str(len(body))}
    response.raise_for_status = MagicMock()
    response.iter_content = MagicMock(return_value=[body])
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


def redirect_response(location: str) -> MagicMock:
    response = MagicMock()
    response.status_code = 302
    response.headers = {"Location": location}
    response.__enter__ = MagicMock(return_value=response)
    response.__exit__ = MagicMock(return_value=False)
    return response


def mock_targz_fetcher(body: bytes) -> tuple[ArchiveFetcher, MagicMock]:
    """ArchiveFetcher wired to a MagicMock session that returns *body*."""
    fetcher = ArchiveFetcher()
    session = MagicMock()
    session.get = MagicMock(return_value=body_response(body))
    fetcher._session = session
    return fetcher, session


def test_fetch_https_follows_allowed_redirect(tmp_path: Path) -> None:
    fetcher = ArchiveFetcher(allowed_redirect_hosts={"github.com", "githubusercontent.com"})
    session = MagicMock()
    session.get = MagicMock(
        side_effect=[
            redirect_response("https://objects.githubusercontent.com/x/demo.tar.gz"),
            body_response(demo_targz()),
        ]
    )
    fetcher._session = session
    dest = tmp_path / "demo.tar.gz"
    fetcher.fetch_https("https://github.com/acme/demo/releases/download/v1/demo.tar.gz", dest)
    assert dest.read_bytes() == demo_targz()
    assert session.get.call_count == 2


def test_fetch_https_refuses_offsite_redirect(tmp_path: Path) -> None:
    fetcher = ArchiveFetcher(allowed_redirect_hosts={"github.com", "githubusercontent.com"})
    session = MagicMock()
    session.get = MagicMock(return_value=redirect_response("https://evil.example.com/demo.tar.gz"))
    fetcher._session = session
    with pytest.raises(InstallError, match="refusing redirect off allowed hosts"):
        fetcher.fetch_https(
            "https://github.com/acme/demo/releases/download/v1/demo.tar.gz",
            tmp_path / "demo.tar.gz",
        )


def test_download_and_extract_happy_path_mock_https(tmp_path: Path) -> None:
    """Mock HTTPS download; extract a tar.gz that contains the named binary."""
    fetcher, session = mock_targz_fetcher(demo_targz())
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


def test_download_and_extract_sha256_match_ok(tmp_path: Path) -> None:
    import hashlib

    body = demo_targz()
    digest = hashlib.sha256(body).hexdigest()
    fetcher, _session = mock_targz_fetcher(body)
    dest = tmp_path / "out"
    fetcher.download_and_extract(
        "https://example.com/releases/demo.tar.gz",
        dest,
        binary="demo",
        asset_name="demo.tar.gz",
        sha256=digest.upper(),  # case-insensitive
    )
    assert (dest / "demo").is_file()


def test_download_and_extract_sha256_mismatch_rejected(tmp_path: Path) -> None:
    body = demo_targz()
    fetcher, _session = mock_targz_fetcher(body)
    with pytest.raises(InstallError, match="sha256 mismatch"):
        fetcher.download_and_extract(
            "https://example.com/releases/demo.tar.gz",
            tmp_path / "out",
            binary="demo",
            asset_name="demo.tar.gz",
            sha256="0" * 64,
        )
    assert not (tmp_path / "out" / "demo").exists()


@pytest.mark.parametrize("asset", ["demo.tar.gz", "demo.tar.xz", "demo.zip", "demo.gz", "demo"])
def test_download_of_an_html_page_is_an_install_error_and_not_cached(
    tmp_path: Path, asset: str
) -> None:
    """A proxy / login page served with a 200 must never become the cached tool."""
    fetcher, _session = mock_targz_fetcher(b"<html>sign in to continue</html>")
    dest = tmp_path / "out"
    with pytest.raises(InstallError):
        fetcher.download_and_extract(
            f"https://example.com/releases/{asset}",
            dest,
            binary="demo",
            asset_name=asset,
        )
    assert not (dest / "demo").exists()


def test_a_download_past_the_wall_clock_deadline_is_an_install_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("maina.backends.archive.DOWNLOAD_MAX_SECONDS", -1)
    fetcher, _session = mock_targz_fetcher(demo_targz())
    with pytest.raises(InstallError, match="download took over"):
        fetcher.fetch_https("https://example.com/demo.tar.gz", tmp_path / "demo.tar.gz")


def test_github_backend_passes_sha256_from_manifest(tmp_path: Path) -> None:
    from maina.backends.github import GithubBackend
    from maina.core.protocols import InstallContext

    fetcher = RecordingFetcher()
    manifest = Manifest(
        id="demo",
        data={"cli": "demo"},
        install=InstallSpec(
            backend="github",
            repo="acme/demo",
            assets={"linux-x86_64": "demo.tar.gz"},
            binary="demo",
            sha256={"linux-x86_64": "abc123"},
        ),
    )
    backend = GithubBackend(fetcher=fetcher)
    ctx = InstallContext(cache_root=tmp_path / "cache", language=FakeLanguage(tmp_path / "cache"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("maina.backends.github.platform_key", lambda: "linux-x86_64")
        backend.resolve(manifest, version="default", env={}, ctx=ctx)
    assert fetcher.sha256_seen == ["abc123"]


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
            "requires": {"node": "20"},
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
        assert (dest / ".maina-install.lock").is_file()


def test_install_context_is_frozen(tmp_path: Path) -> None:
    """InstallContext is never reassigned after construction (only the
    caller-supplied env dict argument mutates, via prepend_path)."""
    from maina.core.protocols import InstallContext

    ctx = InstallContext(cache_root=tmp_path, language=FakeLanguage(tmp_path))
    with pytest.raises(AttributeError):
        cast("Any", ctx).cache_root = tmp_path / "other"


def test_runtime_lock_creates_a_lock_file(tmp_path: Path) -> None:
    with Installer.runtime_lock(tmp_path, "node", "20"):
        assert (tmp_path / "runtimes" / "node-20" / ".maina-install.lock").is_file()


def test_compose_requires_env_serializes_same_runtime_pin(tmp_path: Path) -> None:
    """Two manifests pinning the same runtime must not race
    ``language.ensure()`` into the shared mise-data directory."""
    active = 0
    overlap = threading.Event()
    guard = threading.Lock()

    class SlowLanguage:
        def ensure(
            self, runtime: str, version: str, *, base_env: dict[str, str] | None = None
        ) -> Path:
            nonlocal active
            _ = base_env
            with guard:
                active += 1
                if active > 1:
                    overlap.set()
            time.sleep(0.05)
            with guard:
                active -= 1
            return tmp_path / "bin" / runtime / version

        def env(
            self, runtime: str, version: str, *, base_env: dict[str, str] | None = None
        ) -> dict[str, str]:
            _ = (runtime, version, base_env)
            raise NotImplementedError

    installer = Installer(cache_root=tmp_path, registry=Registry([]), language=SlowLanguage())
    requires = RequiresSpec.from_mapping({"node": "20"}, tool_id="t")
    threads = [
        threading.Thread(target=installer._compose_requires_env, args=(requires, {}))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not overlap.is_set()


def test_toolrunner_timeout_raises_tool_timeout_error(tmp_path: Path) -> None:
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

    class Pathish(InstallerBackend):
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
    with pytest.raises(ToolTimeoutError, match="timed out") as excinfo:
        runner.run({})
    # A run-phase timeout is a MainaError but not an InstallError.
    assert isinstance(excinfo.value, MainaError)
    assert not isinstance(excinfo.value, InstallError)


def test_toolrunner_executor_smoke(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(
        root / "echo.yaml",
        {"cli": "echo", "install": {"backend": "path"}, "output": {"success_codes": [0]}},
    )
    manifest = Registry(root).load("echo")

    class Pathish(InstallerBackend):
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

        def ensure(self, runtime: str, version: str, **_kw: object) -> Path:
            ensured.append((runtime, version))
            return self.cache_root

        def env(self, runtime: str, version: str, **_kw: object) -> dict[str, str]:
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
    monkeypatch.setattr(rt, "mise_bin", lambda **_k: Path("/fake/mise"))
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
