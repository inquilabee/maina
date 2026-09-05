"""Regression tests: load-time validation gaps closed in the 2026-09-05 review.

Fail-closed on non-str/unhashable keys, eager path-safety, an accurate (not
substring-based) YAML anchor guard, and the remaining scalar_str/eager-bool
gaps — split out of test_manifest_validation_regressions.py to stay under
this repo's module-size gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from helpers import write_yaml
from maina.core.errors import ManifestError
from maina.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any


def make_raw_reg(tmp_path: Path, files: dict[str, str]) -> Registry:
    """Write literal YAML text (not a Python-dict fixture) and return a Registry.

    Needed when the test cares about the *source spelling* of a bare scalar
    (``on:`` vs ``yes:`` both resolve to the same Python ``True`` key, which a
    Python-dict fixture can only ever represent as one key already).
    """
    root = tmp_path / "reg"
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Registry(root)


def make_reg(tmp_path: Path, files: dict[str, dict[str, Any]]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        write_yaml(root / rel, data)
    return Registry(root)


# --- fail-closed on non-str/unhashable keys and eager path-safety (review F4/F5/F6/F7) ---


def test_a_bare_yaml_bool_arg_name_is_rejected_not_a_typeerror(tmp_path: Path) -> None:
    """An unquoted ``yes:`` arg name is read as the bool ``True`` by YAML 1.1,
    which used to reach ``ArgSpec`` construction and crash with a raw
    ``TypeError`` instead of a clean ``ManifestError``."""
    registry = make_raw_reg(
        tmp_path,
        {"t.yaml": "cli: echo\nargs:\n  yes:\n    flag: --yes\n    type: flag\n"},
    )
    with pytest.raises(ManifestError, match="args key True must be a quoted string"):
        registry.load("t")


def test_colliding_bare_yaml_bool_env_keys_are_rejected_not_silently_dropped(
    tmp_path: Path,
) -> None:
    """``on:`` and ``yes:`` both resolve to the Python key ``True`` — the second
    one silently overwrote the first with no error. Reject the non-str key
    instead of quietly losing data."""
    registry = make_raw_reg(
        tmp_path,
        {"t.yaml": 'cli: echo\nenv:\n  on: "1"\n  yes: "2"\n'},
    )
    with pytest.raises(ManifestError, match="env key True must be a quoted string"):
        registry.load("t")


def test_a_non_string_platforms_item_is_rejected_not_a_typeerror(tmp_path: Path) -> None:
    """A stray mapping in the ``platforms`` list (e.g. an indentation slip) used
    to crash ``in`` on the ``VALID_PLATFORMS`` frozenset with an unhashable-type
    ``TypeError`` instead of a clean ``ManifestError``."""
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "echo", "platforms": ["linux", {"macos": True}]}},
    )
    with pytest.raises(ManifestError, match="platforms"):
        registry.load("t")


def test_cli_path_traversal_is_rejected_by_load_itself_not_only_by_property_access(
    tmp_path: Path,
) -> None:
    """``Registry.load()`` must fail closed on its own — a manifest whose ``cli``
    would escape the cache root must not load successfully and only fail once
    something later reads ``.cli``."""
    registry = make_reg(
        tmp_path,
        {"t.yaml": {"cli": "../../escaped/echo"}},
    )
    with pytest.raises(ManifestError, match="not a safe path segment"):
        registry.load("t")


# --- YAML anchor guard counts real anchors, not literal '&' (review F16) -------


def test_many_literal_ampersands_in_a_string_do_not_trip_the_anchor_guard(
    tmp_path: Path,
) -> None:
    """Help text with 65+ literal '&' characters has zero real YAML anchors —
    text.count("&") used to reject this as a false positive."""
    help_text = "&" * 70
    registry = make_raw_reg(
        tmp_path,
        {"t.yaml": f'cli: echo\nargs:\n  x:\n    flag: "--x"\n    help: "{help_text}"\n'},
    )
    assert registry.load("t").cli == "echo"


def test_more_than_64_real_anchors_is_still_rejected(tmp_path: Path) -> None:
    lines = ["cli: echo", "args:", *(f"  a{i}: &anchor{i} {{flag: '--a{i}'}}" for i in range(70))]
    registry = make_raw_reg(tmp_path, {"t.yaml": "\n".join(lines) + "\n"})
    with pytest.raises(ManifestError, match="too many YAML anchors"):
        registry.load("t")


# --- scalar_str / eager bool validation gaps (review F30/F33/F34/F35) ----------


def test_abstract_string_true_is_rejected_eagerly_not_masked_as_a_warning(
    tmp_path: Path,
) -> None:
    registry = make_reg(tmp_path, {"t.yaml": {"cli": "echo", "abstract": "true"}})
    with pytest.raises(ManifestError, match="abstract must be true or false"):
        registry.load("t")


def test_install_backend_rejects_a_bare_yaml_bool(tmp_path: Path) -> None:
    registry = make_raw_reg(tmp_path, {"t.yaml": "cli: echo\ninstall:\n  backend: yes\n"})
    with pytest.raises(ManifestError, match=r"install\.backend must be a quoted string"):
        registry.load("t")


def test_install_repo_rejects_a_bare_yaml_bool(tmp_path: Path) -> None:
    registry = make_raw_reg(
        tmp_path, {"t.yaml": "cli: echo\ninstall:\n  backend: github\n  repo: yes\n"}
    )
    with pytest.raises(ManifestError, match=r"install\.repo must be a quoted string"):
        registry.load("t")


def test_os_map_rejects_an_unknown_os_token(tmp_path: Path) -> None:
    registry = make_reg(
        tmp_path,
        {
            "t.yaml": {
                "cli": "echo",
                "install": {
                    "backend": "github",
                    "repo": "acme/x",
                    "tag": "v1",
                    "os_map": {"linvx": "linux"},
                },
            }
        },
    )
    with pytest.raises(ManifestError, match=r"install\.os_map keys \['linvx'\]"):
        registry.load("t")


def test_output_from_file_rejects_a_bare_yaml_bool(tmp_path: Path) -> None:
    registry = make_raw_reg(tmp_path, {"t.yaml": "cli: echo\noutput:\n  from_file: true\n"})
    with pytest.raises(ManifestError, match=r"output\.from_file must be a quoted string"):
        registry.load("t")
