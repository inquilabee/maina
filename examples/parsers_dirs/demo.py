"""Register a custom general parser via ``parsers_dirs``.

Shows the canonical ``maina.register(...)`` composition path: overlay a local
registry tool that references a parser loaded from disk, then call it through
the returned :class:`~maina.MainaApp` instead of threading a ``Runtime``
through every ``Tool(..., runtime=...)`` call by hand.
"""

from __future__ import annotations

from pathlib import Path

import maina


def main() -> None:
    here = Path(__file__).resolve().parent
    app = maina.register(
        maina.Runtime.preferred(),
        registry_dirs=[here / "tools"],
        parsers_dirs=[here / "parsers"],
    )
    result = app.tool("echo_shout").run(message="hello from parsers_dirs")
    print("ok:", result.ok, "parsed:", result.parsed)


if __name__ == "__main__":
    main()
