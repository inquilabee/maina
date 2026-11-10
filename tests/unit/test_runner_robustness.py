"""Runner robustness: archive binary-location edge cases, cwd/stdin handling —
split out of test_install_and_output_regressions.py to stay under this
repo's module-size gate."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from helpers import write_yaml
from maina.backends import PathBackend
from maina.core.errors import ArgError, InstallError
from maina.core.registry import Registry
from maina.installer import Installer
from maina.runner import ToolRunner
from maina.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


def path_installer(tmp_path: Path, reg: Registry) -> Installer:
    return Installer(cache_root=tmp_path / "cache", registry=reg, backends=[PathBackend()])


def test_locate_binary_needs_the_named_member(tmp_path: Path) -> None:
    from maina.backends.archive import ArchiveFetcher

    tree = tmp_path / "tree"
    tree.mkdir()
    payload = tree / "payload.sh"
    payload.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    payload.chmod(0o755)
    # No member named "shellcheck" -> hard error, not "run the lone executable".
    with pytest.raises(InstallError, match="not in archive"):
        ArchiveFetcher._locate_binary(tree, "shellcheck")
    # Opt in explicitly: the lone executable is accepted, with a warning.
    with pytest.warns(RuntimeWarning, match="allow_any_executable"):
        found = ArchiveFetcher._locate_binary(tree, "shellcheck", allow_any_executable=True)
    assert found == payload


def test_copy_raw_asset_rejects_unsupported_format(tmp_path: Path) -> None:
    from maina.backends.archive import ArchiveFetcher

    src = tmp_path / "tool.tar.bz2"
    src.write_bytes(b"BZh9fake")
    with pytest.raises(InstallError, match="unsupported asset format"):
        ArchiveFetcher.copy_raw_asset(src, tmp_path / "out", name="tool.tar.bz2")


def test_copy_raw_asset_caps_gz_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gzip

    from maina.backends import archive as archive_mod
    from maina.backends.archive import ArchiveFetcher

    monkeypatch.setattr(archive_mod, "EXTRACT_MAX_BYTES", 4096)
    src = tmp_path / "tool.gz"
    src.write_bytes(gzip.compress(b"\0" * 100_000))  # ~100 KB from a tiny .gz
    with pytest.raises(InstallError, match="decompresses past"):
        ArchiveFetcher.copy_raw_asset(src, tmp_path / "out", name="tool.gz")


def test_resolve_cache_root_is_absolute() -> None:
    import pathlib

    root = Runtime.resolve_cache_root(pathlib.Path("rel-cache-xyz"))
    assert root.is_absolute()
    root.rmdir()


def test_run_bad_cwd_raises_tool_exec_error(tmp_path: Path) -> None:
    from maina.core.errors import ToolExecError

    reg = make_reg(
        tmp_path,
        {
            "e.yaml": {
                "cli": "echo",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(reg.load("e"), installer=path_installer(tmp_path, reg))
    with pytest.raises(ToolExecError, match="could not run"):
        runner.run({}, cwd=str(tmp_path / "nope-does-not-exist"))


def test_run_without_stdin_does_not_block(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "c.yaml": {
                "cli": "cat",
                "install": {"backend": "path"},
                "output": {"success_codes": [0]},
            }
        },
    )
    runner = ToolRunner(reg.load("c"), installer=path_installer(tmp_path, reg))
    result = runner.run({}, timeout=5)
    assert result.ok
    assert result.raw == ""


@pytest.mark.parametrize(
    "call",
    [
        {"env": {"A": "x\x00y"}},
        {"env": {"A=B": "1"}},
        {"env": {"": "1"}},
        {"extra": [1, 2]},
        {"stdin": 5},
        {"env_allow": "HOME"},
        {"env_allow": ["HOME", 1]},
        {"env_allow": 5},
        {"extra": {"a": 1}},
        {"cwd": "bad\x00dir"},
    ],
)
def test_bad_call_inputs_raise_arg_error_not_a_stdlib_error(
    tmp_path: Path, call: dict[str, Any]
) -> None:
    reg = make_reg(tmp_path, {"echo.yaml": {"cli": "echo", "install": {"backend": "path"}}})
    runner = ToolRunner(reg.load("echo"), installer=path_installer(tmp_path, reg))
    with pytest.raises(ArgError):
        runner.run({}, **call)


def test_a_nul_byte_in_an_argument_raises_arg_error(tmp_path: Path) -> None:
    args = {"msg": {"positional": True}}
    manifest = {"cli": "echo", "install": {"backend": "path"}, "args": args}
    reg = make_reg(tmp_path, {"echo.yaml": manifest})
    runner = ToolRunner(reg.load("echo"), installer=path_installer(tmp_path, reg))
    with pytest.raises(ArgError, match="NUL"):
        runner.run({"msg": "a\x00b"})


def test_a_bytearray_stdin_is_still_accepted(tmp_path: Path) -> None:
    reg = make_reg(tmp_path, {"cat.yaml": {"cli": "cat", "install": {"backend": "path"}}})
    runner = ToolRunner(reg.load("cat"), installer=path_installer(tmp_path, reg))
    assert runner.run({}, stdin=bytearray(b"hi"), timeout=5).raw == "hi"
