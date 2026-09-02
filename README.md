# maina

Call non-Python CLIs from Python. The registry YAML is the source of truth;
`Tool(name)` loads a manifest and runs it.

```python
from maina import Tool

jscpd = Tool("jscpd")
result = jscpd(path=["src"], min_lines=5)
print(result.raw, result.parsed)
```

Full documentation is in [`docs/`](docs/index.md) (built with MkDocs):
[quickstart](docs/quickstart.md), [manifest schema](docs/manifests.md),
[install backends](docs/backends.md), [parsers](docs/parsers.md),
[extending maina](docs/extending.md), [architecture](docs/architecture.md).
Design notes: `adr/0001-registry-first-tool-runtime.md`,
`adr/0002-runtime-register-extendibility.md`.

## Extendibility

Add local tools, parsers, and installer backends without forking maina.
Built-ins use the same `register(...)` path. Open a PR only when a tool is worth
sharing in the shipped catalog.

```python
from pathlib import Path
import maina

rt = maina.Runtime.preferred()
maina.register(rt, registry_dirs=[Path("tools")])
maina.Tool("mytool", runtime=rt).run(...)
```

Or omit the Runtime and mutate the process session: `maina.register(registry_dirs=[...])`.
Later registry dirs override shipped ids for the same tool id. You can also pass
`parsers=`, `parsers_dirs=`, `installers=`, and `language=`. Parsers implement the
`Parser` protocol (`parse(raw, result)` / any matching callable).

The process-wide session is not thread-safe. For concurrent or nested use pass an
explicit `Tool(..., runtime=rt)`, or scope a swap to a block with
`with maina.isolated(rt): ...` (restores the prior Runtime on exit).

`Tool.run` / the call form take `timeout=<seconds>` and `env_allow=[...]`. With
`env_allow` set, only essential vars (`PATH`, locale, temp, `MISE_DATA_DIR`) plus
the named keys reach the tool subprocess — secrets in `os.environ` stay out of
third-party binaries. A subprocess that overruns raises `ToolTimeoutError`; a
`mise` bootstrap failure raises `RuntimeProvisionError` (both subclass
`InstallError`).

`extends` parents use registry ids (no `.yaml` suffix), e.g. `extends: kubectl`
(not `kubectl-kubectl`). Family roots resolve from `kubectl/kubectl.yaml` as id
`kubectl`. See `examples/local_tool/` (overlay registry) and
`examples/parsers_dirs/` (custom parser via `parsers_dirs`).

Shipped `base.yaml` auto-extends every tool but carries **no** runtime pins, so a
static binary never drags `mise`. The `npm` backend provisions Node on its own.
Pin runtimes where you actually need them:

- in your own overlay `base.yaml` (org-wide), or
- per tool: `requires: { node: "22" }`
- `requires: null` on a child clears every inherited pin;
  `requires: { node: null }` drops just one

Quote version pins in YAML (`python: "3.10"`); unquoted floats are rejected.

A manifest may declare `platforms: [linux, macos]`. On any other host `resolve`
raises `UnsupportedPlatformError` up front, and `maina prepare` (no ids) skips
that tool instead of failing.

For a `github` install, optionally pin asset digests:

```yaml
install:
  backend: github
  sha256:
    linux-x86_64: <hex>
    macos-arm64: <hex>
```

A download whose SHA-256 does not match is rejected before `chmod +x`.

### Catalog notes

Some shipped manifests set flag defaults that differ from upstream CLIs so
parsed/machine output is usable out of the box:

- **eslint** — `format` defaults to `json` (upstream stylish); `no_error_on_unmatched_pattern` defaults to true.
- **jscpd** — `reporters` defaults to `json`; `silent` defaults to true.

Pass the kwargs explicitly to override. Intermediate `git-remote` is callable with
empty args on purpose (`git remote` lists remotes). Prefer `output_format` (not
`output`) when an arg would clash with the top-level `output:` block — see
`kubectl-get`.

## Setup

```bash
uv sync
uv run shipgate install
uv run maina prepare jq
uv run python examples/demo.py
uv run python examples/parsers_dirs/demo.py
```

Tool cache: `~/.cache/maina` (override with `MAINA_CACHE`).

Docs site (local preview):

```bash
uv run mkdocs serve      # http://127.0.0.1:8000
uv run mkdocs build --strict
```

Unit tests are hermetic by default; the network/mise suite is opt-in:

```bash
uv run pytest              # unit only
uv run pytest -m integration
```

## Quality gates (ShipGate)

Code quality is enforced with [ShipGate](https://inquilabee.github.io/shipgate/) using the default **full** suite:

```bash
uv run shipgate install
bash scripts/shipgate-link-src.sh   # src-layout import contracts
uv run shipgate format
uv run shipgate check
uv run shipgate refactor check .
uv run shipgate refactor check --strict src
uv run shipgate refactor fix src
uv run shipgate refactor list
uv run shipgate refactor explain default-get
```

Full `shipgate check` (no `--suite`) is the merge bar.

### Pre-commit

Local commits run ShipGate **format**, **full** `shipgate check`, **refactor --strict**, and unit tests via [pre-commit](https://pre-commit.com/) (same shape as [ShipGate’s dogfood hooks](https://github.com/inquilabee/shipgate/blob/main/.pre-commit-config.yaml)):

```bash
uv sync
pre-commit install
```

Hooks expect a local `.shipgate/` tree and `scripts/shipgate-link-src.sh` (gitignored is fine for personal use).

## CLI

```bash
maina prepare              # ensure runtimes + tools
maina prepare jscpd        # one tool (and its required runtime)
maina                      # Typer package help
maina help                 # same as bare `maina`
maina list                 # concrete (callable) tool ids
maina list kubectl         # children of a parent id (abstract parents ok)
maina run                  # Typer run help
maina run eslint --help    # registry help for that tool
maina run shellcheck --help   # that tool's registry help (also bare: run shellcheck)
maina run jq filter=. null_input
maina run jscpd path=src min_lines=5
maina run kubectl-get resource=pods namespace=default
maina run git-remote-add name=origin url=https://example.com
```

`run` maps `key=value` tokens to `Tool(tool_id).run(**kwargs)`. Repeat a key for
list values; a bare key sets a flag to true. Tokens after `--` become `extra`.
With no kwargs (or `--help` / `-h`), `run <tool_id>` prints that tool's registry help
instead of attempting a run.

Registry arg `type: path` is emit-only metadata (help/docs); values are still
passed as strings. Top-level YAML `name:` / `platforms:` are ignored at load
(not enforced).
