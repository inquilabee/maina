"""Regression tests for the fifth blind review (review E) findings."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from helpers import write_yaml
from maina.core.errors import ManifestError
from maina.core.registry import Registry

if TYPE_CHECKING:
    from pathlib import Path


def make_reg(tmp_path: Path, files: dict[str, Any]) -> Registry:
    root = tmp_path / "reg"
    for rel, data in files.items():
        if isinstance(data, str):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(data, encoding="utf-8")
        else:
            write_yaml(root / rel, data)
    return Registry(root)


# --- review E P1-1: parser: path confinement ---------------------------------


@pytest.mark.parametrize("evil", ["/etc/evil.py", "../evil.py", "sub/../../evil.py"])
def test_parser_ref_traversal_rejected_at_load(tmp_path: Path, evil: str) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {"cli": "t", "install": {"backend": "path"}, "parser": evil},
            "parsers/ok.py": "def parse(raw, result):\n    return raw\n",
        },
    )
    with pytest.raises(ManifestError, match="parser"):
        reg.load("t")


def test_parser_ref_plain_filename_still_resolves(tmp_path: Path) -> None:
    reg = make_reg(
        tmp_path,
        {
            "t.yaml": {"cli": "t", "install": {"backend": "path"}, "parser": "ok.py"},
            "parsers/ok.py": "def parse(raw, result):\n    return raw\n",
        },
    )
    manifest = reg.load("t")
    assert manifest.parser_path is not None
    assert manifest.parser_path.name == "ok.py"
