"""Static catalog validation: the checks ``maina validate`` reports, as a
library call.

Needs a live ``Runtime`` (registered install backends, not just the manifest
shape), so this can't live in ``core/registry/`` without a circular import —
``core.registry`` knows nothing about backends or the ``Tool`` construction
pipeline. A caller embedding maina programmatically gets the same "lint my
catalog" answer ``maina validate`` prints, instead of only reaching it by
parsing that command's text output.
"""

from __future__ import annotations

import warnings
from string import Formatter
from typing import TYPE_CHECKING

from maina.core.errors import MainaError
from maina.core.platform import platform_key_problem
from maina.core.registry import BASE_TOOL_ID
from maina.tool import Tool

if TYPE_CHECKING:
    from pathlib import Path

    from maina.core.registry import Manifest
    from maina.runtime import Runtime

NO_CLI_PROBLEM = "no cli: set (add cli: <name>, or abstract: true for an extends-only base)"


def validate_ids(
    rt: Runtime, tool_ids: list[str] | None = None
) -> tuple[dict[str, list[Path]], dict[str, str | None]]:
    """``(duplicate_id_files, problems)`` for *tool_ids* (default: every id).

    ``duplicate_id_files`` maps a same-root-colliding id to its manifest
    paths (see :meth:`Registry.duplicate_ids`); ``problems`` maps every
    selected id to its one-line problem string, or ``None`` when it is fine.
    """
    if tool_ids:
        ids = tool_ids
    else:
        # iter_ids() also warns about a same-root id collision; the returned
        # duplicate_id_files already reports that, so don't warn twice.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ids = list(rt.registry.iter_ids())
    problems = {tid: validate_one(rt, tid) for tid in sorted(ids)}
    return rt.registry.duplicate_ids(), problems


def validate_one(rt: Runtime, tid: str) -> str | None:
    """A one-line problem string for *tid*, or ``None`` when it is fine."""
    try:
        manifest = Tool(tid, runtime=rt).manifest  # loads + checks parser
    except MainaError as exc:
        return str(exc)
    if not manifest.has_cli:
        inheritance_only = manifest.abstract or manifest.id == BASE_TOOL_ID
        return None if inheritance_only else located(NO_CLI_PROBLEM, manifest)
    return located(concrete_problem(manifest, rt), manifest)


def located(problem: str | None, manifest: Manifest) -> str | None:
    """A static problem with the manifest file it was found in appended.

    A load error already names its own files; these checks run on the merged
    manifest, where the id alone is a derived name and not something to open.
    """
    unlocatable = problem is None or manifest.path is None
    return problem if unlocatable else f"{problem} (in {manifest.path})"


def concrete_problem(manifest: Manifest, rt: Runtime) -> str | None:
    """Problems in a concrete (has-``cli``) manifest, or ``None``."""
    found = install_problem(manifest, rt) or from_file_problem(manifest)
    return None if manifest.abstract else found


def install_problem(manifest: Manifest, rt: Runtime) -> str | None:
    """Static problems in a manifest's ``install:`` block, or ``None``."""
    install = manifest.install
    if install.backend not in rt.installer.backend_names:
        known = ", ".join(sorted(rt.installer.backend_names)) or "none"
        return f"install backend {install.backend!r} is not registered (known: {known})"
    if install.backend == "github" and not (install.repo and install.has_assets()):
        return "install.backend: github needs repo: and assets: (or asset_template:)"
    keys = sorted(set(install.assets) | set(install.sha256))
    if bad := [problem for key in keys if (problem := platform_key_problem(key))]:
        return f"install.assets/sha256 platform keys: {'; '.join(bad)}"
    return None


def from_file_problem(manifest: Manifest) -> str | None:
    """An ``output.from_file`` template that can never resolve, or ``None``."""
    template = manifest.output.from_file
    if not template:
        return None
    fields = {field for _lit, field, _spec, _conv in Formatter().parse(template) if field}
    if undeclared := sorted(fields - {"cwd", *manifest.args}):
        return f"output.from_file names non-arg placeholder(s): {', '.join(undeclared)}"
    # The runner only interpolates scalars, so a flag never substitutes — the
    # template is statically unresolvable, not merely unresolved on some runs.
    if flags := sorted(f for f in fields if f in manifest.args and manifest.args[f].type == "flag"):
        return (
            "output.from_file placeholder(s) name a type: flag arg, which never "
            f"interpolates: {', '.join(flags)}"
        )
    return None
