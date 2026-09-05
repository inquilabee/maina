# wild_catalog example

Not a curated, shipped catalog (`docs/product/vision.md` is explicit: maina
ships no tool catalog) — a dev-time check that the engine actually works
against real, popular, non-Python-implemented CLIs over the network, not just
this repo's own fixtures. Every tool here really installs and runs; nothing
in this directory is exercised by the test suite.

```text
examples/wild_catalog/
  tools/
    trivy.yaml       # github, .tar.gz — vuln/misconfig scanner (Go)
    pandoc.yaml      # github, .tar.gz / .zip — document converter (Haskell)
    dasel.yaml       # github, bare binary — JSON/YAML/TOML/XML/CSV query (Go)
    gh.yaml          # github, .tar.gz / .zip — GitHub's own CLI (Go)
    hyperfine.yaml   # github, .tar.gz — command-line benchmarking (Rust)
    bat.yaml         # github, .tar.gz — cat with syntax highlighting (Rust)
    delta.yaml       # github, .tar.gz — readable git diff viewer (Rust)
    git-cliff.yaml   # github, .tar.gz — changelog from conventional commits (Rust)
    spectral.yaml    # npm — OpenAPI/AsyncAPI linter (Node)
  demo.py
```

8 `github`-backend manifests (covering `.tar.gz`, `.zip`, and bare-binary
release assets — `ArchiveFetcher`'s different extraction paths) and 1 `npm`
manifest, deliberately spanning both real install backends this repo doesn't
otherwise exercise live in one place. Release asset names were read from each
project's GitHub releases API / `npm view` live on 2026-09-05 (see the
comment atop each manifest) — pins will need bumping later like any dependency.

```python
from pathlib import Path
import maina

app = maina.register(maina.Runtime.preferred(use_env=False), registry_dirs=[here / "tools"])
app.tool("dasel").run(selector="name", in_format="json", stdin='{"name": "maina"}')
```

Run (needs network — downloads ~9 real binaries/packages into `~/.cache/maina`,
reused on later runs): `uv run python examples/wild_catalog/demo.py`
