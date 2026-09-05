"""Install and run 9 real, non-Python-implemented CLIs through maina.

Unlike examples/catalog/ (which stays offline on purpose), every tool here
actually downloads and executes over the network — this is the "does the
engine work on real CLIs, not just our own fixtures" check, not a curated,
shipped catalog (see docs/product/vision.md: maina ships no tool catalog).
Network access (github.com, registry.npmjs.org) and a Node on PATH are
required; nothing here is exercised by the test suite (deliberately).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import maina


def run(app: maina.MainaApp, tool_id: str, /, **kwargs: Any) -> None:
    result = app.tool(tool_id).run(**kwargs)
    first_line = (result.raw or "").strip().splitlines()[:1]
    print(f"{tool_id:12} ok={result.ok!s:5} {first_line[0] if first_line else ''}")


VERSION_FLAG = "--version"
VERSION_ONLY_TOOLS = ("trivy", "gh", "bat", "delta", "git-cliff")


def main() -> None:
    here = Path(__file__).resolve().parent
    app = maina.register(maina.Runtime.preferred(use_env=False), registry_dirs=[here / "tools"])

    for tool_id in VERSION_ONLY_TOOLS:
        run(app, tool_id, extra=VERSION_FLAG)

    run(app, "pandoc", from_format="markdown", to_format="plain", stdin="# Hello\n\nWorld\n")
    run(app, "dasel", selector="name", in_format="json", stdin='{"name": "maina", "ok": true}')
    run(app, "hyperfine", command_to_run="echo hi", warmup="1", min_runs="2")

    with tempfile.TemporaryDirectory() as tmp:
        doc = Path(tmp) / "doc.yaml"
        ruleset = Path(tmp) / "ruleset.yaml"
        doc.write_text('openapi: "3.0.0"\ninfo:\n  title: demo\n  version: "1.0"\npaths: {}\n')
        ruleset.write_text("rules: {}\n")
        run(app, "spectral", documents=[str(doc)], ruleset=str(ruleset))


if __name__ == "__main__":
    main()
