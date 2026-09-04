"""Regression tests: install identity hashing, manifest field validation, registry diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from helpers import write_yaml
from maina.core.errors import ManifestError
from maina.core.registry import InstallSpec, Registry
from maina.runtime import Runtime

if TYPE_CHECKING:
    from pathlib import Path


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


# --- review D: install identity + manifest validation --------------------------


def test_cache_version_covers_requires_for_npm(tmp_path: Path) -> None:
    from maina.installer import Installer

    base = {"cli": "jscpd", "version": "4.0.5", "install": {"backend": "npm", "package": "jscpd"}}
    reg = make_reg(
        tmp_path,
        {
            "a.yaml": {**base, "requires": {"node": "18"}},
            "b.yaml": {**base, "requires": {"node": "22"}},
        },
    )
    assert Installer._cache_version(reg.load("a")) != Installer._cache_version(reg.load("b"))


def test_identity_key_covers_binary_and_allow_scripts() -> None:
    base = InstallSpec(backend="github", repo="acme/x", tag="v1", assets={"k": "x.tgz"})
    other_binary = InstallSpec(
        backend="github", repo="acme/x", tag="v1", assets={"k": "x.tgz"}, binary="x"
    )
    scripts_on = InstallSpec(backend="npm", package="x", allow_scripts=True)
    scripts_off = InstallSpec(backend="npm", package="x")
    assert base.identity_key() != other_binary.identity_key()
    assert scripts_on.identity_key() != scripts_off.identity_key()


def test_install_package_name_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "npm", "package": "evil; rm -rf /"},
            }
        },
    )
    with pytest.raises(ManifestError, match="not a valid npm name"):
        reg.load("t")


def test_install_scoped_package_name_accepted(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "npm", "package": "@scope/pkg.js"},
            }
        },
    )
    assert reg.load("t").install.package == "@scope/pkg.js"


def test_install_tag_traversal_rejected_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "github", "repo": "acme/t", "tag": "../../etc"},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"install\.tag"):
        reg.load("t")


def test_requires_key_charset_validated_at_load(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "install": {"backend": "path"},
                "requires": {"no de;x": "20"},
            }
        },
    )
    with pytest.raises(ManifestError, match=r"\[A-Za-z0-9_\.\+-\]"):
        reg.load("t")


# --- review D: MAINA_REGISTRY diagnostics -------------------------------------


def test_env_registry_missing_dir_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAINA_REGISTRY", str(tmp_path / "does-not-exist"))
    with pytest.warns(RuntimeWarning, match="not a directory"):
        Runtime._env_registry_dirs()
