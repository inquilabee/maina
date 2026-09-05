# scripts_dir example

Wrap a repo-local script — or a small script package — as a maina tool
without forking maina: `install.backend: script` + `install.entry:`.

```text
examples/scripts_dir/
  tools/
    deploy.yaml            # single file: scripts/deploy.sh
    release.yaml            # directory: scripts/release/ (release.py + helper.py)
    scripts/
      deploy.sh
      release/
        release.py
        helper.py
  demo.py
```

A single file is chmod +x'd and exec'd directly — its own shebang
(`#!/usr/bin/env bash`, `#!/usr/bin/env python3`, ...) picks the interpreter,
and a `requires:`-pinned runtime (not used in this offline demo) is already
on `PATH` by the time it runs. A directory needs exactly one file named like
the directory itself (any/no extension) as its entry point; sibling files are
free to import/source each other however their language allows —
`release.py` imports `helper.py` from the same directory.

`entry:` here is a bare name, so it resolves against the nearest `scripts/`
directory walking up from the manifest that declared it (same convention as
a family `parsers/` — see `examples/parsers_dirs/`). A `/`-containing entry
(`entry: shared/release.py`) resolves against the registry root instead.

```python
from pathlib import Path
import maina

here = Path("examples/scripts_dir")
app = maina.register(maina.Runtime.preferred(use_env=False), registry_dirs=[here / "tools"])
app.tool("deploy").run(target="prod")
app.tool("release").run()
```

Run: `uv run python examples/scripts_dir/demo.py`
