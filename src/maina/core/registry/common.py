"""Shared manifest-parsing helpers (no dependency on the spec classes)."""

from __future__ import annotations

from typing import Any

from maina.core.errors import ManifestError

MANIFEST_PREFIX = "manifest "

# A safe path segment: no separators / NUL, no leading "." (covers "." / ".."),
# no leading "-" (would be read as a flag when used as argv[0] or a `which` arg),
# and no shell-glob metacharacters (would match arbitrary files under rglob).
UNSAFE_SEGMENT_CHARS = "/\\\x00*?[]"


def manifest_error(subject: str, detail: str) -> ManifestError:
    """A ``ManifestError`` for *subject* (a tool id) with a consistent prefix."""
    return ManifestError(f"{MANIFEST_PREFIX}{subject!r} {detail}")


def optional_str(value: Any) -> str | None:
    """Coerce optional YAML scalars to str, preserving None."""
    return None if value is None else str(value)


def require_bool(value: Any, *, field_name: str, tool_id: str) -> bool:
    """A boolean-or-absent field; ``"true"`` / ``1`` are rejected, not coerced.

    ``abstract: "true"`` must not silently yield a *concrete* tool, and
    ``required: 1`` must not silently mean ``False``.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise manifest_error(
            tool_id, f"{field_name} must be true or false (a YAML boolean), not {value!r}"
        )
    return value


def require_optional_bool(value: Any, *, field_name: str, tool_id: str) -> bool | None:
    """Like :func:`require_bool` but keeps "unset" distinct from ``False``.

    For a tri-state field (``repeat``) where absent means "engine default",
    not "off" — but ``"no"`` / ``0`` are still rejected, not coerced.
    """
    return None if value is None else require_bool(value, field_name=field_name, tool_id=tool_id)


def scalar_str(value: Any, *, field_name: str, tool_id: str) -> str:
    """Coerce a YAML scalar to ``str``, rejecting ``bool``, ``float``, and ``None``.

    YAML 1.1 turns ``yes`` / ``on`` into booleans and ``3.10`` into the float
    ``3.1``; a field that names a download, a cache path, or a subprocess env
    value must be quoted, not silently mangled. Callers where ``None`` is a
    meaningful "unset" (an optional field, an inheritance drop marker) must
    handle it before calling this — it is never a valid string value.
    """
    if value is None:
        raise manifest_error(
            tool_id, f"{field_name} must be a quoted string, not null (a bare 'key:' is null)"
        )
    if isinstance(value, bool):
        raise manifest_error(
            tool_id, f"{field_name} must be a quoted string, not a YAML boolean ({value!r})"
        )
    if isinstance(value, float):
        raise manifest_error(
            tool_id,
            f"{field_name} must be a quoted string (YAML float {value!r} loses precision; "
            f'quote it like {field_name}: "3.10")',
        )
    return str(value)


def require_str_keys(raw: dict[str, Any], *, block_name: str, tool_id: str) -> None:
    """Reject a non-``str`` mapping key.

    YAML 1.1 reads a bare ``yes`` / ``no`` / ``on`` / ``off`` / ``true`` /
    ``false`` (or an unquoted number) key as ``bool`` / ``int``, not ``str`` —
    silently colliding two differently-spelled keys (``env: {on: "1", yes:
    "2"}`` both become the key ``True``) onto one Python key and dropping the
    other, with no error. Reject the type instead of quietly losing data or
    crashing downstream on a non-``str`` dict key: quoting is the only way to
    mean the literal word.
    """
    for key in raw:
        if not isinstance(key, str):
            raise manifest_error(
                tool_id,
                f"{block_name} key {key!r} must be a quoted string (a bare "
                "yes/no/on/off/true/false/number key is read as that type, not text)",
            )


def unknown_keys(raw: dict[str, Any], allowed: frozenset[str]) -> list[str]:
    """Return sorted unknown mapping keys relative to an allowlist.

    Keys are stringified first: YAML 1.1 turns ``on:`` / ``yes:`` / ``no:`` /
    ``off:`` into booleans and ``1:`` into an int, so a raw ``sorted()`` over
    mixed key types would raise ``TypeError`` — which is not a ``ManifestError``
    and would escape every load guard. Stringifying keeps such a manifest a
    clean, skippable load error.
    """
    return sorted(str(key) for key in raw if key not in allowed)


def is_unsafe_segment(text: str) -> bool:
    return (
        not text
        or text != text.strip()
        or text[0] in ".-"
        or any(char in text for char in UNSAFE_SEGMENT_CHARS)
    )


def safe_segment(value: Any, *, field_name: str, tool_id: str, allow_none: bool = False) -> Any:
    """Reject a manifest value that is not a single safe path segment.

    ``cli`` / ``version`` / ``install.binary`` are interpolated into cache
    paths and release URLs; ``..``, separators, a leading ``.`` or a NUL would
    let a manifest write outside the cache root, and an unquoted YAML scalar
    (``version: 3.10`` → float ``3.1``, ``cli: yes`` → ``True``) would silently
    pick the wrong artifact.
    """
    if value is None:
        if allow_none:
            return None
        raise manifest_error(tool_id, f"missing {field_name}")
    text = scalar_str(value, field_name=field_name, tool_id=tool_id)
    if is_unsafe_segment(text):
        raise manifest_error(tool_id, f"{field_name} {text!r} is not a safe path segment")
    return text
