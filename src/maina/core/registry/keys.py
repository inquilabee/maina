"""The manifest vocabulary: every key and enum value the loader accepts.

Kept apart from the spec classes that enforce them because two other things
read the vocabulary directly — :mod:`maina.core.registry.jsonschema` projects
it into a JSON Schema, and the loader fails closed on anything outside it — so
adding a key here is the one edit that makes it real everywhere.
"""

from __future__ import annotations

# Dropped from the merged manifest before use (pure catalog decoration).
# ``platforms`` is kept and enforced at resolve time (see ``Manifest.platforms``).
IGNORED_MANIFEST_KEYS = frozenset({"name"})
BASE_TOOL_ID = "base"
CURRENT_SCHEMA = 1

# Every key a merged manifest may carry. Validated at load so a typo
# (``outputs:``, ``platform:``, ``timeoutt:``) fails closed like the nested
# blocks, instead of loading silently and being ignored.
TOP_LEVEL_KEYS = frozenset(
    {
        "schema",
        "cli",
        "version",
        "subcommand",
        "extends",
        "abstract",
        "platforms",
        "timeout",
        "separator",
        "parser",
        "env",
        "args",
        "install",
        "requires",
        "output",
        "name",
    }
)

ARG_KEYS = frozenset(
    {
        "type",
        "flag",
        "joiner",
        "positional",
        "required",
        "repeat",
        "join",
        "order",
        "default",
        "description",
        "help",
        "choices",
    }
)
ARG_TYPES = frozenset({"string", "flag", "path", "pairs"})
JOIN_VALUES = frozenset({"csv"})
VALID_PLATFORMS = frozenset({"linux", "macos", "windows"})

INSTALL_KEYS = frozenset(
    {
        "backend",
        "repo",
        "assets",
        "binary",
        "tag",
        "package",
        "sha256",
        "allow_scripts",
        "allow_any_executable",
    }
)

OUTPUT_KEYS = frozenset({"format", "success_codes", "from_file", "allow_stale_report"})

# Names ``Tool.run`` / ``__call__`` bind as their own parameters — a manifest
# arg with one of these names is still valid, but the caller must pass it via
# ``run(args={"env": ...})`` rather than ``run(env=...)``.
RESERVED_RUN_PARAMS = frozenset(
    {"extra", "parser", "cwd", "timeout", "env_allow", "env", "stdin", "text", "args"}
)
