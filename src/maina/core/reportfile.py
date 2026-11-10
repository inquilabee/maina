"""Read an ``output.from_file`` report into a ToolResult."""

from __future__ import annotations

import string
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maina.core.argparser import ArgParser

if TYPE_CHECKING:
    from maina.core.registry import Manifest
    from maina.core.result import ToolResult


class ReportReader:
    """Owns the ``output.from_file`` contract for one manifest."""

    # Cap on a report slurped into memory (download / extract have their own caps).
    MAX_BYTES = 64 * 1024 * 1024
    # How far a report's mtime may predate the run that should have written it
    # before it is rejected as a leftover. Slack, not precision: filesystem mtime
    # granularity and clock offsets (NFS, container bind mounts) make an exact
    # comparison unusable. A tool that legitimately leaves a report alone sets
    # ``output.allow_stale_report: true``.
    MTIME_SLACK = 2.0

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest

    def apply(
        self, result: ToolResult, kwargs: dict[str, Any], cwd: str | None, started_wall: float
    ) -> None:
        """Point the parser at ``output.from_file`` instead of stdout.

        ``result.raw`` is left as the tool's stdout; the file contents go on
        ``result.report``. Every failure (unresolved template, escape, missing
        file, a report older than this run) is surfaced on ``result.parse_error``
        — never a silent fallback.
        """
        template = self.manifest.output.from_file
        if not template:
            return
        base = (Path(cwd) if cwd else Path.cwd()).resolve()
        path, error = self._resolve_path(template, base, kwargs)
        if path is None:
            result.parse_error = error
            return
        if not path.is_file():
            result.parse_error = f"output.from_file not found: {path}"
            result.meta["parsed_from_missing"] = str(path)
            return
        stat = path.stat()
        result.meta |= {"parsed_from": str(path), "parsed_from_mtime": stat.st_mtime}
        stale = stat.st_mtime < started_wall - self.MTIME_SLACK
        if stale and not self.manifest.output.allow_stale_report:
            result.parse_error = (
                f"output.from_file {path} was not written by this run (stale) — set "
                "output.allow_stale_report: true if the tool leaves an unchanged report alone"
            )
            return
        if stat.st_size > self.MAX_BYTES:
            result.parse_error = (
                f"output.from_file {path} is {stat.st_size} bytes, over the {self.MAX_BYTES} cap"
            )
            return
        result.report = path.read_text(encoding="utf-8", errors="replace")

    def _resolve_path(
        self, template: str, base: Path, kwargs: dict[str, Any]
    ) -> tuple[Path | None, str | None]:
        """Interpolate ``output.from_file`` to a path under *base* (``(path, error)``)."""
        resolved = ArgParser(self.manifest.args, tool_id=self.manifest.id).resolved_values(kwargs)
        subs = {
            key: value
            for key, value in resolved.items()
            if key != "cwd" and isinstance(value, (str, int, float)) and not isinstance(value, bool)
        }
        try:
            self.check_template(template)
            rel = template.format(cwd=str(base), **subs)
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            return None, f"output.from_file template {template!r} unresolved: {exc}"
        path = (base / rel).resolve()
        return (
            (path, None)
            if path.is_relative_to(base)
            else (None, f"output.from_file escapes the run directory: {path}")
        )

    @staticmethod
    def check_template(template: str) -> None:
        """Reject an ``output.from_file`` placeholder that reaches past a bare name.

        ``{cwd}`` and ``{argname}`` are fine; ``{cwd.__class__…}`` / ``{a[0]}``
        are not — the template only names a directory and args. A conversion or
        format spec (``{out!r}``, ``{out:>1000000000}``) is rejected too: it can
        drive a huge allocation inside ``str.format`` and buys nothing here.
        """
        for _literal, field, spec, conv in string.Formatter().parse(template):
            if field is None:
                continue
            if set("[.") & set(field):
                raise ValueError(f"placeholder {field!r} may not use attribute or item access")
            if spec or conv:
                raise ValueError(f"placeholder {field!r} may not carry a conversion or format spec")
