"""Worked catalog: github + npm + output.from_file + a subcommand family.

Only ``hello`` (a ``path`` tool) actually runs — everything else is shown via
``Tool.argv()`` / ``Tool.describe()`` so the demo stays offline.
"""

from __future__ import annotations

from pathlib import Path

import maina
from maina import Tool


def main() -> None:
    rt = maina.Runtime.preferred(use_env=False)
    maina.register(rt, registry_dirs=[Path(__file__).resolve().parent])

    print("registered:", rt.registry.list_commands(None))
    print()

    # A real run of the path-backed tool.
    result = Tool("hello", runtime=rt).run(words=["hello", "from", "maina"])
    print("hello ->", repr(result.raw.strip()), "ok:", result.ok)
    print()

    # github backend — argv the run would build (argv[0] resolves to the cache).
    jq = Tool("jq", runtime=rt)
    print("jq argv:", jq.argv(filter=".items[].name", path="data.json", raw=True))

    # npm backend + output.from_file — the parser reads the report file.
    jscpd = Tool("jscpd", runtime=rt)
    print("jscpd from_file:", jscpd.describe()["output"]["from_file"])
    print("jscpd argv:", jscpd.argv(path="src"))

    # subcommand family: git/remote/add.yaml -> id "git-remote-add".
    gra = Tool("git-remote-add", runtime=rt)
    print("git-remote-add argv:", gra.argv(name="origin", url="https://example.com"))


if __name__ == "__main__":
    main()
