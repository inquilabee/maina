# wild_catalog example

Not a curated, shipped catalog (`docs/product/vision.md` is explicit: maina
ships no tool catalog) — a dev-time check that the engine actually works
against real, popular, non-Python-implemented CLIs over the network and the
local machine, not just this repo's own fixtures. Every one of the 30 tools
below really resolves/installs and does real, meaningful work through maina
— a real lint finding, a real diff, a real HTTP response, a real encrypted
round trip — not a bare `--version` check (`kubectl` is the one exception:
it skips gracefully with a clear message when it isn't on `PATH` and no
cluster is assumed, rather than faking a pass). Nothing in this directory is
exercised by the test suite.

```text
examples/wild_catalog/
  tools/                 # 30 manifests, one per tool (see table below)
  demo.py
```

24 `github`-backend manifests (covering `.tar.gz`, `.zip`, and bare-binary
release assets — `ArchiveFetcher`'s different extraction paths), 3 `npm`
manifests, and 3 `path`-backend manifests (`git`, `docker`, `kubectl` —
realistically always already installed, never fetched as a release asset;
the one corner of the install-backend surface the other 27 tools don't
exercise). Release asset names and CLI flags were read live against each
project's current release / npm package on 2026-09-05 (see the comment atop
each manifest) — pins will need bumping later like any dependency.

| Tool | Backend | Category | Real demo call |
| --- | --- | --- | --- |
| trivy | github | security | misconfig scan of a real Dockerfile (offline, bundled policies) |
| syft | github | security | software bill of materials for a real directory |
| grype | github | security | vulnerability scan (first run pulls a database, ~40s) |
| cosign | github | security | offline keypair generation |
| sops | github | security | encrypt a real file to a real age recipient |
| age / age-keygen | github | security | real keygen + encrypt/decrypt round trip |
| gh | github | git/vcs | real unauthenticated GitHub API call |
| git-cliff | github | git/vcs | real changelog from this repo's own commit history |
| delta | github | git/vcs | render a real unified diff |
| k6 | github | load testing | real (offline) trivial script run |
| tflint | github | IaC lint | lint a real (intentionally incomplete) Terraform file |
| golangci-lint | github | CI/lint | list configured linters (no Go toolchain assumed) |
| actionlint | github | CI/lint | lint a real GitHub Actions workflow |
| buf | github | CI/lint | lint a real `.proto` file |
| pandoc | github | docs | convert real Markdown from stdin |
| dasel | github | data | query real JSON from stdin |
| hyperfine | github | CLI utility | benchmark a real command |
| bat | github | CLI utility | syntax-highlight real stdin |
| glow | github | CLI utility | render real Markdown from stdin |
| eza | github | CLI utility | list a real directory (Linux only — no macOS asset) |
| dust | github | CLI utility | disk usage of a real directory |
| difftastic | github | CLI utility | structural diff of two real files |
| xh | github | CLI utility | real HTTP GET |
| spectral | npm | API lint | lint a real inline OpenAPI doc |
| commitlint | npm | git/vcs | lint a real conventional-commit message |
| stylelint | npm | CI/lint | lint real CSS |
| git | path | git/vcs | real `log` against this repo's own history |
| docker | path | containers | real `docker ps` (read-only — may run on a shared machine) |
| kubectl | path | containers | real client-side dry-run apply, or a clean skip if absent |

```python
from pathlib import Path
import maina

app = maina.register(maina.Runtime.preferred(use_env=False), registry_dirs=[here / "tools"])
app.tool("dasel").run(selector="name", in_format="json", stdin='{"name": "maina"}')
```

Run (needs network — downloads ~27 real binaries/packages into
`~/.cache/maina`, reused on later runs; grype's first run is the one
intentionally slow step; `git`/`docker`/`kubectl` resolve from `PATH`
instead): `uv run python examples/wild_catalog/demo.py`
