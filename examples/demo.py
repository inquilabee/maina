"""Smoke demo for the Tool API.

maina ships no tool catalog: you register one. This demo overlays the two
example registries in this folder and runs a path-backed tool offline.
"""

from __future__ import annotations

from pathlib import Path

import maina


def main() -> None:
    here = Path(__file__).resolve().parent
    app = maina.register(
        maina.Runtime.preferred(),
        registry_dirs=[here / "local_tool", here / "parsers_dirs" / "tools"],
        parsers_dirs=[here / "parsers_dirs" / "parsers"],
    )

    print(app.tool().help())
    print("---")
    print("registered:", app.runtime.registry.list_commands(None))
    print("---")

    result = app.tool("echo_shout").run(message="hello from maina")
    print("ok:", result.ok, "raw:", repr(result.raw.strip()), "parsed:", result.parsed)


if __name__ == "__main__":
    main()
