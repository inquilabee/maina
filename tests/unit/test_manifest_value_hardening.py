"""Manifest values that used to escape as raw exceptions or reach the filesystem."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.core.errors import InstallError, ManifestError
from maina.core.registry import Registry
from maina.installer import Installer

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


def load(tmp_path: Path, data: dict[str, Any]) -> Any:
    root = tmp_path / "reg"
    write_yaml(root / "t.yaml", data)
    return Registry(root).load("t")


def load_env(tmp_path: Path, data: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = load(tmp_path, data).env
    return env


def test_an_unbalanced_asset_template_is_a_manifest_error(tmp_path: Path) -> None:
    install = {"backend": "github", "repo": "a/b", "asset_template": "tool-{version"}
    with pytest.raises(ManifestError, match="asset_template"):
        load(tmp_path, {"cli": "t", "version": "1", "install": install})


def test_one_bad_manifest_does_not_break_listing_the_good_ones(tmp_path: Path) -> None:
    root = tmp_path / "reg"
    write_yaml(root / "good.yaml", {"cli": "echo", "install": {"backend": "path"}})
    write_yaml(
        root / "bad.yaml",
        {"cli": "x", "install": {"backend": "github", "repo": "a/b", "asset_template": "a{"}},
    )
    with pytest.warns(RuntimeWarning, match="bad"):
        assert Registry(root).list_commands(None) == ["good"]


def test_a_requires_pin_cannot_name_a_path(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="safe version"):
        load(
            tmp_path,
            {"cli": "t", "install": {"backend": "path"}, "requires": {"node": "20/../../x"}},
        )


def test_runtime_lock_never_leaves_the_cache_root(tmp_path: Path) -> None:
    with (
        pytest.raises(InstallError, match="not a safe cache directory"),
        Installer.runtime_lock(tmp_path / "cache", "node", "20/../../../outside"),
    ):
        pytest.fail("lock must not be taken")
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("package", ["PyYAML", "black[d]", "ruamel.yaml", "my_pkg"])
def test_pip_package_names_follow_pep_508(tmp_path: Path, package: str) -> None:
    manifest = load(
        tmp_path,
        {"cli": "t", "version": "1", "install": {"backend": "pip", "package": package}},
    )
    assert manifest.install.package == package


def test_a_pip_package_cannot_carry_shell_or_path_text(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="valid pip name"):
        load(
            tmp_path,
            {"cli": "t", "version": "1", "install": {"backend": "pip", "package": "a/../b"}},
        )


@pytest.mark.parametrize("bad", [{"env": {"A": {"b": "c"}}}, {"subcommand": ["a"]}])
def test_container_values_are_rejected_where_a_string_is_required(
    tmp_path: Path, bad: dict[str, Any]
) -> None:
    with pytest.raises(ManifestError, match=r"not a (dict|list)"):
        load_env(tmp_path, {"cli": "t", "install": {"backend": "path"}, **bad})


def test_a_trailing_newline_does_not_pass_the_package_name_check(tmp_path: Path) -> None:
    install = {"backend": "pip", "package": "black\n"}
    with pytest.raises(ManifestError, match="valid pip name"):
        load(tmp_path, {"cli": "t", "version": "1", "install": install})


def test_runtime_lock_works_when_the_cache_directory_is_a_symlink(tmp_path: Path) -> None:
    real = tmp_path / "bigdisk"
    real.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "runtimes").symlink_to(real)
    with Installer.runtime_lock(cache, "node", "20"):
        assert (real / "node-20" / ".maina-install.lock").exists()
