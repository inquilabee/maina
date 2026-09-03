"""Load built-in and generic output parsers."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from maina.core.errors import ManifestError, ParseError
from maina.core.protocols import Parser

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from maina.core.result import ToolResult

    ParserInput = Parser | Callable[[str, ToolResult], Any]


@dataclass(slots=True, frozen=True)
class FunctionParser:
    """Concrete :class:`~maina.core.protocols.Parser` wrapping a plain callable."""

    _fn: Callable[[str, ToolResult], Any]

    def __call__(self, raw: str, result: ToolResult) -> Any:
        return self._fn(raw, result)


def parser(fn: ParserInput) -> Parser:
    """Return a concrete Parser for *fn*.

    Plain ``(raw, result) -> Any`` callables are wrapped in :class:`FunctionParser`.
    Existing :class:`FunctionParser` instances are returned unchanged.
    """
    if isinstance(fn, FunctionParser):
        return fn
    if not isinstance(fn, Parser):
        raise ManifestError("parser factory requires a Parser callable")
    return FunctionParser(fn)  # type: ignore[arg-type]


def parse_json(raw: str, _result: ToolResult) -> Any:
    """Parse JSON strictly. ``None`` for empty output; raises on malformed JSON."""
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"tool output is not valid JSON: {exc}") from exc


def parse_json_lenient(raw: str, _result: ToolResult) -> Any:
    """Parse JSON when possible; otherwise return the stripped text (or None)."""
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


def builtin_generals() -> dict[str, Parser]:
    """Shipped general parser map (shared by Runtime bootstrap and tests)."""
    return {
        "json": parser(parse_json),
        "json-lenient": parser(parse_json_lenient),
        "lines": parser(parse_lines),
        "text": parser(parse_text),
        "raw": parser(parse_text),
    }


class ParserResolver:
    """Owns the general parser name→callable map (:class:`~maina.core.protocols.Parser`).

    Call :meth:`bind` for a run-scoped resolver that applies override / ref / path.
    """

    def __init__(self, *, generals: dict[str, Parser] | None = None) -> None:
        self._generals: dict[str, Parser] = dict(generals) if generals is not None else {}

    def bind(
        self,
        *,
        parser_ref: str | None = None,
        parser_path: Path | None = None,
        override: ParserInput | str | None = None,
    ) -> BoundParser:
        """Return a run-scoped binder sharing this instance's general map."""
        return BoundParser(
            self,
            parser_ref=parser_ref,
            parser_path=parser_path,
            override=override,
        )

    def register(self, name: str, fn: ParserInput) -> None:
        """Register or replace a general parser by mode name."""
        self._validate_name(name)
        self._generals[name] = self._require_parser(name, fn)

    def register_many(self, parsers: Mapping[str, ParserInput]) -> None:
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

    def generic_parser(self, mode: str) -> Parser:
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
    def _require_parser(name: str, fn: object) -> Parser:
        """Validate *fn* satisfies :class:`Parser`; wrap via :func:`parser`."""
        if not isinstance(fn, Parser):
            raise ManifestError(f"parser {name!r} must be a Parser callable")
        return parser(fn)

    _MODULE_CACHE: ClassVar[dict[tuple[str, int, int], Parser]] = {}

    @classmethod
    def load_parser_module(cls, path: Path) -> Parser:
        """Load ``parse`` from a registry ``parsers/*.py`` file (cached by mtime/size)."""
        try:
            stat = path.stat()
        except OSError as exc:
            raise ManifestError(f"cannot load parser module {path}: {exc}") from exc
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if (cached := cls._MODULE_CACHE.get(key)) is not None:
            return cached
        spec = importlib.util.spec_from_file_location(f"maina_parser_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise ManifestError(f"cannot load parser module {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        parse = getattr(module, "parse", None)
        resolved = ParserResolver._require_parser(str(path), parse)
        cls._MODULE_CACHE[key] = resolved
        return resolved

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
        override: ParserInput | str | None = None,
    ) -> None:
        self._registry = registry
        self.parser_ref = parser_ref
        self.parser_path = parser_path
        self.override = override

    def resolve(self) -> Parser | None:
        """Resolve the effective parser callable for a run.

        A ``parser:`` ref that is neither a registered general parser nor a
        resolved ``*.py`` path raises rather than silently yielding ``None``.
        """
        if self.override is not None:
            return (
                self._registry.generic_parser(self.override)
                if isinstance(self.override, str)
                else parser(self.override)
            )
        if self.parser_path is not None:
            return ParserResolver.load_parser_module(self.parser_path)
        if self.parser_ref is None:
            return None
        if not self._registry.is_general(self.parser_ref):
            raise ManifestError(
                f"unknown parser {self.parser_ref!r}: not a registered general parser "
                "or a *.py module"
            )
        return self._registry.generic_parser(self.parser_ref)

    def apply(self, result: ToolResult) -> ToolResult:
        """Run the configured parser and attach ``parsed`` / ``parse_error``.

        The parser is invoked even when stdout is empty (a linter that reports
        only on stderr is still parseable). A parser that raises never crashes
        ``run()``: the message lands in ``result.parse_error`` and ``parsed``
        stays ``None``.
        """
        resolved = self.resolve()
        if resolved is None:
            result.parsed = None
            return result
        try:
            result.parsed = resolved(result.parse_input, result)
        except ParseError as exc:
            result.parsed = None
            result.parse_error = str(exc)
        except Exception as exc:  # ruff:ignore[blind-except] - a parser must not break the run
            result.parsed = None
            result.parse_error = f"{type(exc).__name__}: {exc}"
        return result
