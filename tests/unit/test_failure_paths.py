"""Failure paths that must surface as MainaError (or a clear message), not raw stdlib errors."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from helpers import FakePopen
from maina.backends.archive import ArchiveFetcher
from maina.backends.path import PathBackend
from maina.core.argparser import ArgParser
from maina.core.errors import InstallError, ManifestError, RegisterError, ToolExitError
from maina.core.protocols import InstallContext
from maina.core.registry import Manifest, Registry
from maina.core.result import ToolResult
from maina.core.subprocess_utils import kill_tree, run_capped
from maina.runtime import Runtime


def test_ctrl_c_during_a_run_kills_the_whole_tool_tree() -> None:
    timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGINT))
    started = time.monotonic()
    timer.start()
    with pytest.raises(KeyboardInterrupt):
        run_capped(
            ["sh", "-c", "sleep 30 & wait"],
            env={"PATH": "/usr/bin:/bin"},
            timeout=None,
            max_bytes=1024,
        )
    timer.cancel()
    assert time.monotonic() - started < 10


def test_kill_tree_falls_back_to_kill_without_a_real_process_group() -> None:
    proc = FakePopen(["x"])
    kill_tree(cast("subprocess.Popen[bytes]", proc))
    assert proc.killed


def test_an_uncreatable_cache_root_is_a_register_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RegisterError, match="MAINA_CACHE"):
        Runtime.resolve_cache_root(blocker / "cache")


def test_a_download_that_cannot_be_written_is_an_install_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    fetcher = ArchiveFetcher(session=MagicMock())
    with pytest.raises(InstallError, match="could not install"):
        fetcher.download_and_extract(
            "https://example.com/demo", blocker / "out", binary="demo", asset_name="demo"
        )


def test_a_failing_run_reports_the_last_line_of_stderr() -> None:
    result = ToolResult(
        tool="t", ok=False, exit_code=3, raw="", stderr="warn\n\nfatal: bad input\n", command=["t"]
    )
    with pytest.raises(ToolExitError, match=r"exited with code 3: fatal: bad input"):
        result.raise_for_status()


def test_extra_accepts_a_path_alone_or_in_a_list() -> None:
    assert ArgParser.normalize_extra(Path("a/b")) == ["a/b"]
    assert ArgParser.normalize_extra(["x", Path("y")]) == ["x", "y"]


def test_an_empty_registry_error_names_the_next_step(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="MAINA_REGISTRY"):
        Registry([]).load("jq")


def test_a_missing_path_binary_suggests_an_install_block(tmp_path: Path) -> None:
    manifest = Manifest(id="nope", data={"cli": "definitely-not-installed-xyz"})
    ctx = InstallContext(cache_root=tmp_path, language=MagicMock())
    with pytest.raises(InstallError, match="install: block"):
        PathBackend().resolve(manifest, version="default", env={"PATH": "/nonexistent"}, ctx=ctx)
