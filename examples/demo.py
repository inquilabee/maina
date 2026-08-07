"""Smoke demo for the Tool API."""

from __future__ import annotations

from pathlib import Path

import pyporter
from pyporter import Tool


def main() -> None:
    # Optional overlay (same register path as shipped builtins):
    local = Path(__file__).resolve().parent / "local_tool"
    if local.is_dir():
        pyporter.register(registry_dirs=[local])

    Tool().help()
    print("---")
    Tool("jscpd").help()
    print("---")
    print("commands(kubectl):", Tool("kubectl").commands())
    print("git remote add id:", Tool("git", subcommand="remote/add").id)

    result = Tool("jq").run(filter=".", null_input=True)
    print("jq ok:", result.ok, "raw:", repr(result.raw.strip()), "parsed:", result.parsed)


if __name__ == "__main__":
    main()
