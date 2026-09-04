"""The typed ``requires:`` block (runtime name → version string)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from maina.core.errors import ManifestError

from .common import MANIFEST_PREFIX, manifest_error


@dataclass(slots=True, frozen=True)
class RequiresSpec:
    """Typed ``requires:`` map — runtime name → version string.

    Inheritance: child pins merge onto parents; ``requires: null`` replaces (clears)
    inherited pins; a ``<runtime>: null`` drops that one pin from the merge.
    """

    runtimes: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str | None:
        """Return the pinned version for *name*, or None."""
        return self.runtimes.get(name)

    @classmethod
    def from_mapping(cls, raw: Any, *, tool_id: str) -> RequiresSpec:
        """Parse a ``requires`` map (runtime → version); fail closed on non-maps.

        ``None`` means no pins (used after ``requires: null`` clears inheritance).
        """
        return cls._from_pins(cls._require_map(raw, tool_id=tool_id), tool_id=tool_id)

    @classmethod
    def layer_for_merge(cls, raw: Any, *, tool_id: str) -> dict[str, str | None] | None:
        """Normalize a layer's ``requires`` for inheritance merge.

        Returns ``None`` when the layer sets ``requires: null`` (replace/clear).
        A ``<runtime>: null`` value is preserved as a drop marker.
        """
        return (
            None
            if raw is None
            else cls._pins_for_merge(cls._require_map(raw, tool_id=tool_id), tool_id=tool_id)
        )

    @staticmethod
    def _require_map(raw: Any, *, tool_id: str) -> dict[str, Any]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise manifest_error(tool_id, "requires must be a map")
        return raw

    @classmethod
    def _from_pins(cls, raw: dict[str, Any], *, tool_id: str) -> RequiresSpec:
        """Parse freeform runtime→version pins (final resolved map; no nulls)."""
        pins: dict[str, str] = {}
        for name, value in raw.items():
            cls._require_runtime_key(name, tool_id=tool_id)
            if value is None:
                raise ManifestError(
                    cls._pin_message(
                        tool_id,
                        name,
                        "must be a version string (use requires: null to clear inherited pins)",
                    )
                )
            pins[name] = cls._version_scalar(value, tool_id=tool_id, key=name)
        return cls(runtimes=pins)

    @classmethod
    def _pins_for_merge(cls, raw: dict[str, Any], *, tool_id: str) -> dict[str, str | None]:
        """Parse freeform pins for merge; ``null`` values mark pins to drop."""
        pins: dict[str, str | None] = {}
        for name, value in raw.items():
            cls._require_runtime_key(name, tool_id=tool_id)
            pins[name] = (
                None if value is None else cls._version_scalar(value, tool_id=tool_id, key=name)
            )
        return pins

    # A runtime name reaches ``mise`` argv and a ``command -v`` fallback; keep it
    # to characters that cannot carry a shell or path surprise.
    _RUNTIME_KEY_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")

    @classmethod
    def _require_runtime_key(cls, name: Any, *, tool_id: str) -> None:
        if not isinstance(name, str) or not cls._RUNTIME_KEY_RE.match(name):
            raise manifest_error(tool_id, "requires keys must match [A-Za-z0-9_.+-]+")

    @staticmethod
    def _pin_message(tool_id: str, key: str, detail: str) -> str:
        return f"{MANIFEST_PREFIX}{tool_id!r} requires.{key} {detail}"

    @classmethod
    def _version_scalar(cls, value: Any, *, tool_id: str, key: str) -> str:
        """Coerce a version pin; reject YAML floats and non-str/int scalars.

        Unquoted YAML ``3.10`` becomes float ``3.1`` — authors must quote pins
        (``python: "3.10"``).
        """
        if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
            kind = type(value).__name__
            raise ManifestError(
                cls._pin_message(
                    tool_id, key, f'must be a version string (quote pins like "3.10"; got {kind})'
                )
            )
        if isinstance(value, float):
            raise ManifestError(
                cls._pin_message(
                    tool_id,
                    key,
                    f"must be a quoted version string (YAML float {value!r} loses precision; "
                    f'quote like {key}: "3.10")',
                )
            )
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str):
            return value
        raise ManifestError(
            cls._pin_message(tool_id, key, f"must be a version string (got {type(value).__name__})")
        )
