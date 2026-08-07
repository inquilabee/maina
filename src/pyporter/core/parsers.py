"""Load built-in and generic output parsers."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeAlias

from pyporter.core.errors import ManifestError
from pyporter.core.protocols import Parser
from pyporter.core.result import ToolResult

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# Annotation-friendly callable form; structurally matches :class:`Parser`.
ParserFn: TypeAlias = Callable[[str, ToolResult], Any]


def parse_json(raw: str, _result: ToolResult) -> Any:
    """Parse JSON when possible; otherwise return stripped text or None."""
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_lines(raw: str, _result: ToolResult) -> list[str]:
    """Split raw stdout into lines."""
    return raw.splitlines()


def parse_text(raw: str, _result: ToolResult) -> str:
    """Return raw stdout unchanged."""
    return raw


def builtin_generals() -> dict[str, ParserFn]:
    """Shipped general parser map (shared by Runtime bootstrap and tests)."""
    return {
        "json": parse_json,
        "lines": parse_lines,
        "text": parse_text,
        "raw": parse_text,
    }


class ParserResolver:
    """Owns the general parser name→callable map (:class:`~pyporter.core.protocols.Parser`).

    Call :meth:`bind` for a run-scoped resolver that applies override / ref / path.
    """

    def __init__(self, *, generals: dict[str, ParserFn] | None = None) -> None:
        self._generals: dict[str, ParserFn] = dict(generals) if generals is not None else {}

    def bind(
        self,
        *,
        parser_ref: str | None = None,
        parser_path: Path | None = None,
        override: ParserFn | str | None = None,
    ) -> BoundParser:
        """Return a run-scoped binder sharing this instance's general map."""
        return BoundParser(
            self,
            parser_ref=parser_ref,
            parser_path=parser_path,
            override=override,
        )

    def register(self, name: str, fn: ParserFn) -> None:
        """Register or replace a general parser by mode name."""
        self._validate_name(name)
        self._generals[name] = self._require_parser(name, fn)

    def register_many(self, parsers: Mapping[str, ParserFn]) -> None:
        """Register or replace general parsers from a name→callable map."""
        for name, fn in parsers.items():
            self.register(name, fn)

    def load_parsers_dirs(self, dirs: Sequence[Path]) -> None:
        """Load ``*.py`` modules defining ``parse``; register under file stem."""
        for directory in dirs:
            paths = sorted(path for path in directory.glob("*.py") if not path.name.startswith("_"))
            for path in paths:
                self.register(path.stem, self.load_parser_module(path))

    def is_general(self, name: str) -> bool:
        """Return True when *name* is a registered general parser on this instance."""
        return name in self._generals

    def generic_parser(self, mode: str) -> ParserFn:
        """Return a registered general parser by mode name."""
        if mode not in self._generals:
            raise ManifestError(f"unknown generic parser mode: {mode!r}")
        return self._generals[mode]

    @staticmethod
    def _validate_name(name: str) -> None:
        """Reject invalid general parser names."""
        if not name or name.endswith(".py") or "/" in name or "\\" in name:
            raise ManifestError(f"invalid general parser name: {name!r}")

    @staticmethod
    def _require_parser(name: str, fn: object) -> ParserFn:
        """Validate *fn* satisfies :class:`Parser`; return it as :data:`ParserFn`."""
        if not isinstance(fn, Parser):
            raise ManifestError(f"parser {name!r} must be a Parser callable")
        return fn  # type: ignore[return-value]

    @staticmethod
    def load_parser_module(path: Path) -> ParserFn:
        """Load ``parse`` from a registry ``parsers/*.py`` file."""
        spec = importlib.util.spec_from_file_location(f"pyporter_parser_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ManifestError(f"cannot load parser module {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parse = getattr(module, "parse", None)
        return ParserResolver._require_parser(str(path), parse)

    # Back-compat aliases for call sites that still reference classmethods.
    parse_json = staticmethod(parse_json)
    parse_lines = staticmethod(parse_lines)
    parse_text = staticmethod(parse_text)


class BoundParser:
    """Run-scoped parser bind/apply state over a :class:`ParserResolver` map."""

    def __init__(
        self,
        registry: ParserResolver,
        *,
        parser_ref: str | None = None,
        parser_path: Path | None = None,
        override: ParserFn | str | None = None,
    ) -> None:
        self._registry = registry
        self.parser_ref = parser_ref
        self.parser_path = parser_path
        self.override = override

    def resolve(self) -> ParserFn | None:
        """Resolve the effective parser callable for a run."""
        return (
            (
                self._registry.generic_parser(self.override)
                if isinstance(self.override, str)
                else self.override
            )
            if self.override is not None
            else (
                ParserResolver.load_parser_module(self.parser_path)
                if self.parser_path is not None
                else (
                    self._registry.generic_parser(self.parser_ref)
                    if self.parser_ref is not None and self._registry.is_general(self.parser_ref)
                    else None
                )
            )
        )

    def apply(self, result: ToolResult) -> ToolResult:
        """Attach parsed output onto result when a parser is configured."""
        resolved = self.resolve()
        if resolved is None or not result.raw:
            result.parsed = None
            return result
        result.parsed = resolved(result.raw, result)
        return result
