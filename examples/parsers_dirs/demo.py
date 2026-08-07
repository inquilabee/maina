"""Register a custom general parser via ``parsers_dirs``.

Shows the canonical ``pyporter.register(rt, ...)`` composition path:
overlay a local registry tool that references a parser loaded from disk.
"""

from __future__ import annotations

from pathlib import Path

import pyporter


def main() -> None:
    here = Path(__file__).resolve().parent
    rt = pyporter.Runtime.preferred()
    pyporter.register(
        rt,
        registry_dirs=[here / "tools"],
        parsers_dirs=[here / "parsers"],
    )
    result = pyporter.Tool("echo_shout", runtime=rt).run(message="hello from parsers_dirs")
    print("ok:", result.ok, "parsed:", result.parsed)


if __name__ == "__main__":
    main()
