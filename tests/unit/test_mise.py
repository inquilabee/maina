"""MiseRuntime unit tests with fakes (no network / bootstrap)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from maina.mise import MiseRuntime


def test_mise_env_prepends_ensure_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rt = MiseRuntime(cache_root=tmp_path)
    bin_dir = tmp_path / "runtimes" / "node" / "bin"
    bin_dir.mkdir(parents=True)
    ensured: list[tuple[str, str]] = []

    def fake_ensure(runtime: str, version: str, **_kw: object) -> Path:
        ensured.append((runtime, version))
        return bin_dir

    monkeypatch.setattr(rt, "ensure", fake_ensure)
    monkeypatch.setenv("PATH", "/usr/bin")
    env = rt.env("node", "20.11.0")
    assert ensured == [("node", "20.11.0")]
    path_parts = env["PATH"].split(os.pathsep)
    assert path_parts[0] == str(bin_dir)
    assert env["MISE_DATA_DIR"] == str(tmp_path / "mise-data")


def test_mise_bin_bootstrap_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maina.core.errors import RuntimeProvisionError
    from maina.mise import MiseRuntime

    monkeypatch.setattr("maina.mise.shutil.which", lambda *_a, **_k: None)
    monkeypatch.delenv("MAINA_ALLOW_MISE_BOOTSTRAP", raising=False)
    rt = MiseRuntime(cache_root=tmp_path / "cache")
    with pytest.raises(RuntimeProvisionError, match="MAINA_ALLOW_MISE_BOOTSTRAP"):
        rt.mise_bin()


def which_only(*names: str) -> object:
    """A ``shutil.which`` fake resolving only the given command names."""
    return lambda name, **_k: f"/usr/bin/{name}" if name in names else None


def test_mise_which_fallback_disables_exec_auto_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """present_bin_dir (and ensure's own pre-install check) share this lookup
    and must never trigger a real install — `mise exec`'s auto-install has to
    stay off on the fallback path, even though `ensure()` wants a real
    install for its own, separate, explicit `mise install` call."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        argv: list[str], *, env: dict[str, str] | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((list(argv), dict(env or {})))
        if "which" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        return subprocess.CompletedProcess(argv, 0, "/opt/node/bin/node\n", "")

    monkeypatch.setattr("maina.mise.subprocess.run", fake_run)
    result = MiseRuntime._mise_which_uncaught(
        Path("/usr/bin/mise"), "node@20", "node", {"PATH": "/usr/bin"}
    )
    assert result == Path("/opt/node/bin")
    assert len(calls) == 2
    which_argv, _which_env = calls[0]
    exec_argv, exec_env = calls[1]
    assert "which" in which_argv
    assert "exec" in exec_argv
    assert exec_env["MISE_EXEC_AUTO_INSTALL"] == "false"


def test_present_bin_dir_never_calls_mise_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The readiness probe (`prepare --check`) must never provision anything,
    even through the mise-which fallback's exec path."""
    rt = MiseRuntime(cache_root=tmp_path)
    monkeypatch.setattr("maina.mise.shutil.which", lambda *_a, **_k: "/usr/bin/mise")

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        assert "install" not in argv
        return subprocess.CompletedProcess(argv, 1, "", "")  # nothing resolves

    monkeypatch.setattr("maina.mise.subprocess.run", fake_run)
    assert rt.present_bin_dir("node", "20") is None


def test_bootstrap_mise_argv_and_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact curl/sh argv and the MISE_INSTALL_PATH / MISE_VERSION env
    the bootstrap depends on — this RCE-adjacent path had no coverage beyond
    the opt-in gate."""
    calls: list[list[str]] = []
    target = tmp_path / "mise" / "bin" / "mise"

    def fake_run(
        argv: list[str], *, env: dict[str, str] | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        if argv[0].endswith("curl"):
            return subprocess.CompletedProcess(argv, 0, "#!/bin/sh\necho installed\n", "")
        assert env is not None
        assert env["MISE_INSTALL_PATH"] == str(target)
        assert env["MISE_VERSION"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/bin/sh\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr("maina.mise.subprocess.run", fake_run)
    monkeypatch.setattr("maina.mise.shutil.which", which_only("curl", "sh"))
    result = MiseRuntime._bootstrap_mise(target, {"PATH": "/usr/bin"})
    assert result == target
    assert calls[0][:2] == ["/usr/bin/curl", "-fsSL"]
    assert calls[1][0] == "/usr/bin/sh"


def test_bootstrap_mise_timeout_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maina.core.errors import RuntimeProvisionError

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=600)

    monkeypatch.setattr("maina.mise.subprocess.run", fake_run)
    monkeypatch.setattr("maina.mise.shutil.which", which_only("curl", "sh"))
    with pytest.raises(RuntimeProvisionError, match="timed out"):
        MiseRuntime._bootstrap_mise(tmp_path / "mise" / "bin" / "mise", {"PATH": "/usr/bin"})


def test_bootstrap_mise_failure_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from maina.core.errors import RuntimeProvisionError

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        if argv[0].endswith("curl"):
            return subprocess.CompletedProcess(argv, 0, "#!/bin/sh\n", "")
        raise subprocess.CalledProcessError(1, argv, "", "install script failed")

    monkeypatch.setattr("maina.mise.subprocess.run", fake_run)
    monkeypatch.setattr("maina.mise.shutil.which", which_only("curl", "sh"))
    with pytest.raises(RuntimeProvisionError, match="failed to bootstrap mise"):
        MiseRuntime._bootstrap_mise(tmp_path / "mise" / "bin" / "mise", {"PATH": "/usr/bin"})
