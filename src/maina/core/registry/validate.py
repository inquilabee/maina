"""Manifest-shape validation rules that don't need Registry's I/O or caches.

Split out of ``loader.py`` to keep that module under this repo's module-size
cap. Pure functions over a merged manifest dict — no file access, no state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from maina.core.errors import ManifestError

from .common import (
    manifest_error,
    require_bool,
    require_str_keys,
    safe_segment,
    scalar_str,
    unknown_keys,
)
from .keys import CURRENT_SCHEMA, TOP_LEVEL_KEYS, VALID_PLATFORMS

# A manifest is a small hand-written config; a wall of real anchors is a
# mistake, not a legitimate hand-written shape.
MAX_YAML_ANCHORS = 64


def validate_top_level(tid: str, merged: dict[str, Any]) -> None:
    """Fail closed on unknown keys and bad schema / timeout / platforms values."""
    if bad := unknown_keys(merged, TOP_LEVEL_KEYS):
        raise manifest_error(tid, f"unknown top-level keys: {', '.join(bad)}")
    schema = merged.get("schema")
    if schema is not None and schema != CURRENT_SCHEMA:
        raise manifest_error(
            tid, f"schema {schema!r} unsupported (maina understands schema {CURRENT_SCHEMA})"
        )
    # Eager, like every other bool field — `abstract: "true"` (a string)
    # must not load clean and only surface as a masked warning later.
    require_bool(merged.get("abstract"), field_name="abstract", tool_id=tid)
    validate_timeout(tid, merged.get("timeout"))
    validate_platforms(tid, merged.get("platforms"))
    validate_separator_and_from_file(tid, merged)
    validate_scalar_fields(tid, merged)


def validate_scalar_fields(tid: str, merged: dict[str, Any]) -> None:
    """Force validation for scalar fields the resolved ``Manifest`` otherwise
    reads through lazy properties (``cli``, ``version``, ``env``), so a bad
    value fails at ``load()``, not at first property access or first
    download. ``cli`` / ``version`` also get the same ``safe_segment``
    path-traversal check their properties run — a manifest whose ``cli``
    would resolve outside the cache root must not load cleanly and only
    fail once something reads ``.cli``.
    """
    for field_name in ("cli", "version"):
        if merged.get(field_name) is not None:
            safe_segment(merged[field_name], field_name=field_name, tool_id=tid)
    env = merged.get("env")
    if isinstance(env, dict):
        require_str_keys(env, block_name="env", tool_id=tid)
        for key, value in env.items():
            scalar_str(value, field_name=f"env.{key}", tool_id=tid)


def validate_separator_and_from_file(tid: str, merged: dict[str, Any]) -> None:
    """``separator`` shape / ``order`` conflict, and no ``cwd`` arg with ``from_file``."""
    args = merged.get("args") or {}
    validate_separator(tid, merged.get("separator"), args)
    output = merged.get("output") or {}
    if isinstance(output, dict) and output.get("from_file") and "cwd" in args:
        raise manifest_error(tid, "an arg cannot be named 'cwd' when output.from_file is set")


def validate_separator(tid: str, sep: Any, args: Any) -> None:
    if sep is None:
        return
    if not isinstance(sep, str) or not sep:
        raise manifest_error(tid, f"separator {sep!r} must be a non-empty string")
    if not isinstance(args, dict):
        return
    # `order: null` is the null-drop idiom clearing an inherited order, not a
    # declaration of one — it must not trip the conflict it just resolved.
    if ordered := [
        n for n, a in args.items() if isinstance(a, dict) and a.get("order") is not None
    ]:
        raise manifest_error(
            tid,
            "separator and args.order are two ways to lay out argv — pick one "
            f"(args with order: {', '.join(map(str, ordered))})",
        )


def validate_timeout(tid: str, timeout: Any) -> None:
    if timeout is None:
        return
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise manifest_error(tid, f"timeout {timeout!r} must be a number of seconds")
    if timeout <= 0:
        raise manifest_error(tid, f"timeout {timeout!r} must be > 0 (omit it for no limit)")


def validate_platforms(tid: str, platforms: Any) -> None:
    if platforms is None:
        return
    if not isinstance(platforms, list):
        raise manifest_error(tid, f"platforms {platforms!r} must be a list, e.g. [linux, macos]")
    # isinstance first: an unhashable item (e.g. a stray mapping from a YAML
    # indentation slip) must not crash `in` on the membership check below.
    if unknown := sorted(
        str(p) for p in platforms if not isinstance(p, str) or p not in VALID_PLATFORMS
    ):
        allowed = ", ".join(sorted(VALID_PLATFORMS))
        raise manifest_error(tid, f"platforms {unknown} not in {{{allowed}}}")


def validate_relative_ref(ref: str, *, kind: str) -> None:
    """A ``parser:`` / ``install.entry`` ref is a relative path — no ``..``, no
    absolute path.

    A bare filename (``add.py``) resolves under the declaring manifest's family
    directory (``parsers/`` or ``scripts/``); a ``/``-containing path
    (``shared/util.py``) resolves under the manifest's registry root instead
    (see ``Registry._resolve_family_or_root_ref``). Either way the resolved
    path is exec'd, so a manifest must not be able to point it outside its
    registry root. *kind* names the ref in the error message (``"parser"``,
    ``"script entry"``).
    """
    parts = ref.replace("\\", "/").split("/")
    if (
        ref != ref.strip()
        or "\x00" in ref
        or ref.startswith("/")
        or ".." in parts
        or Path(ref).is_absolute()
    ):
        raise ManifestError(f"{kind} {ref!r} must be a relative path with no '..' segments")


def count_yaml_anchors(text: str) -> int:
    """Count real ``&name`` anchor declarations, not the literal ``&`` character.

    An ordinary description or a URL query string can easily contain 65+
    literal ``&`` characters with zero real anchors, which a
    ``text.count("&")`` guard rejects as a false positive. Composing (not
    constructing) the document exposes the actual anchor table PyYAML's own
    ``Composer`` builds; ``Composer.compose_document`` clears it right after
    composing (for multi-document streams), so this reads it before that
    happens rather than via the ``compose()`` convenience function. Aliases
    resolve to the *same* node object as their anchor, so this table already
    reflects real anchor *declarations* regardless of how many aliases
    reference each one.
    """
    loader = yaml.SafeLoader(text)
    try:
        loader.get_event()  # STREAM-START
        if loader.check_event(yaml.StreamEndEvent):
            return 0
        loader.get_event()  # DOCUMENT-START
        loader.compose_node(None, None)
        return len(loader.anchors)
    finally:
        loader.dispose()
