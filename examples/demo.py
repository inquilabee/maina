"""Smoke demo for the Tool API.

maina ships no tool catalog: you register one. This demo overlays the two
example registries in this folder and runs a path-backed tool offline.
"""

from __future__ import annotations

from pathlib import Path

import maina
from maina import Tool


def main() -> None:
    here = Path(__file__).resolve().parent
    rt = maina.Runtime.preferred()
    maina.register(
        rt,
        registry_dirs=[here / "local_tool", here / "parsers_dirs" / "tools"],
        parsers_dirs=[here / "parsers_dirs" / "parsers"],
    )

    print(Tool(runtime=rt).help())
    print("---")
    print("registered:", rt.registry.list_commands(None))
    print("---")

    result = Tool("echo_shout", runtime=rt).run(message="hello from maina")
    print("ok:", result.ok, "raw:", repr(result.raw.strip()), "parsed:", result.parsed)


if __name__ == "__main__":
    main()
