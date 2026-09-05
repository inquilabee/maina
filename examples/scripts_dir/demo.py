"""Wrap a repo-local shell script and a script package as maina tools.

Shows the ``script`` install backend: a single file (``deploy.sh``) execs
directly via its own shebang; a directory (``release/``) needs one file named
like the directory itself as its entry point, and is free to import siblings.
"""

from __future__ import annotations

from pathlib import Path

import maina


def main() -> None:
    here = Path(__file__).resolve().parent
    app = maina.register(maina.Runtime.preferred(use_env=False), registry_dirs=[here / "tools"])

    deploy = app.tool("deploy").run(target="prod")
    print("deploy ->", repr(deploy.raw.strip()), "ok:", deploy.ok)

    release = app.tool("release").run()
    print("release ->", repr(release.raw.strip()), "ok:", release.ok)


if __name__ == "__main__":
    main()
