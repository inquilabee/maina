"""``install.asset_template`` — one pattern instead of six asset names."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from helpers import write_yaml
from maina.core.errors import ManifestError
from maina.core.registry import InstallSpec, Registry

if TYPE_CHECKING:
    from pathlib import Path


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


def gitleaks_install(**extra: Any) -> dict[str, Any]:
    return {
        "backend": "github",
        "repo": "gitleaks/gitleaks",
        "asset_template": "gitleaks_{version}_{os}_{arch}.tar.gz",
        **extra,
    }


def test_the_template_expands_per_platform_with_the_projects_own_spelling(
    tmp_path: Path,
) -> None:
    """A project that names every asset to one pattern should say it once; six
    near-identical lines can drift apart."""
    registry = make_reg(
        tmp_path,
        {
            "gitleaks.yaml": {
                "cli": "gitleaks",
                "version": "8.30.1",
                "install": gitleaks_install(arch_map={"x86_64": "x64"}, os_map={"macos": "darwin"}),
            }
        },
    )
    install = registry.load("gitleaks").install
    assert install.asset_for("linux-x86_64", "8.30.1") == "gitleaks_8.30.1_linux_x64.tar.gz"
    assert install.asset_for("macos-arm64", "8.30.1") == "gitleaks_8.30.1_darwin_arm64.tar.gz"
    assert install.asset_for("windows-x86_64", "8.30.1") == "gitleaks_8.30.1_windows_x64.tar.gz"


def test_an_explicit_asset_overrides_the_template(tmp_path: Path) -> None:
    """One platform that breaks the pattern must not force every name to be
    written out."""
    registry = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "gitleaks",
                "version": "8.30.1",
                "install": gitleaks_install(assets={"windows-x86_64": "gitleaks-odd-name.zip"}),
            }
        },
    )
    install = registry.load("t").install
    assert install.asset_for("windows-x86_64", "8.30.1") == "gitleaks-odd-name.zip"
    assert install.asset_for("linux-x86_64", "8.30.1") == "gitleaks_8.30.1_linux_x86_64.tar.gz"


def test_no_template_and_no_entry_resolves_to_nothing(tmp_path: Path) -> None:
    spec = InstallSpec(backend="github", repo="a/b")
    assert spec.asset_for("linux-x86_64", "1.0") is None
    assert spec.has_assets() is False
    del tmp_path


def test_an_unknown_placeholder_is_rejected_at_load(tmp_path: Path) -> None:
    """Otherwise the typo only surfaces as a 404 partway through an install."""
    registry = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "version": "1.0",
                "install": {
                    "backend": "github",
                    "repo": "a/b",
                    "asset_template": "t_{verison}_{os}.tar.gz",
                },
            }
        },
    )
    with pytest.raises(ManifestError, match="placeholder 'verison' is not one of"):
        registry.load("t")


def test_a_format_spec_in_the_template_is_rejected(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "version": "1.0",
                "install": {
                    "backend": "github",
                    "repo": "a/b",
                    "asset_template": "t_{version:>99999999}.tar.gz",
                },
            }
        },
    )
    with pytest.raises(ManifestError, match="may not carry a conversion or format spec"):
        registry.load("t")


def test_a_token_map_value_must_be_a_safe_path_segment(tmp_path: Path) -> None:
    """``arch_map`` values are interpolated into a download URL and a filename."""
    registry = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "t",
                "version": "1.0",
                "install": gitleaks_install(arch_map={"x86_64": "../../etc"}),
            }
        },
    )
    with pytest.raises(ManifestError, match="is not a safe path segment"):
        registry.load("t")


def test_a_token_map_must_be_a_map(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "t", "version": "1.0", "install": gitleaks_install(os_map="linux")}},
    )
    with pytest.raises(ManifestError, match=r"install\.os_map must be a map"):
        registry.load("t")


def test_the_template_is_part_of_the_cache_identity() -> None:
    """Two manifests sharing cli+version but fetching different assets must not
    share a cache directory."""
    one = InstallSpec(backend="github", repo="a/b", asset_template="t_{version}_{os}.tar.gz")
    two = InstallSpec(backend="github", repo="a/b", asset_template="t-{version}-{os}.zip")
    mapped = InstallSpec(
        backend="github",
        repo="a/b",
        asset_template="t_{version}_{os}.tar.gz",
        arch_map={"x86_64": "x64"},
    )
    assert one.identity_key() != two.identity_key()
    assert one.identity_key() != mapped.identity_key()
