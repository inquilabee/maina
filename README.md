# maina

Call non-Python CLIs from Python. You describe a tool once in a YAML manifest;
`Tool(name)` loads it, installs/resolves the executable, maps keyword arguments
to argv, runs the process, and returns a structured result.

maina ships **the engine and the manifest contract, not a tool catalog** —
you register your own manifests (or your app does).

```python
from pathlib import Path
import maina
from maina import Tool

rt = maina.Runtime.preferred()
maina.register(rt, registry_dirs=[Path("my_tools")])   # your manifests

result = Tool("jscpd", runtime=rt)(path=["src"], min_lines=5)
print(result.raw, result.parsed)
```

Full documentation is in [`docs/`](docs/index.md) (built with MkDocs):
[quickstart](docs/quickstart.md), [manifest schema](docs/manifests.md),
[install backends](docs/backends.md), [parsers](docs/parsers.md),
[extending maina](docs/extending.md), [architecture](docs/architecture.md).
Design notes: `adr/0001-registry-first-tool-runtime.md`,
`adr/0002-runtime-register-extendibility.md`.

## Extendibility

Register tools, parsers, and installer backends on a `Runtime`.

```python
from pathlib import Path
import maina

rt = maina.Runtime.preferred()
maina.register(rt, registry_dirs=[Path("tools")])
maina.Tool("mytool", runtime=rt).run(...)
```

Or omit the Runtime and mutate the process session: `maina.register(registry_dirs=[...])`.
A later registry dir overrides an earlier one for the same tool id. You can also
pass `parsers=`, `parsers_dirs=`, `installers=`, and `language=`. Parsers
implement the `Parser` protocol (`parse(raw, result)` / any matching callable).

The process-wide session is not thread-safe. For concurrent or nested use pass an
explicit `Tool(..., runtime=rt)`, or scope a swap to a block with
`with maina.isolated(rt): ...` (restores the prior Runtime on exit).

`Tool.run` / the call form take `timeout=<seconds>` (bounds the tool subprocess),
`env_allow=[...]`, and `env={...}` (extra per-call variables, layered after the
trim; the manifest's own `env:` block still wins a collision). With `env_allow`
set, only a fixed essential set plus the named keys reach the tool subprocess
(and the install-phase subprocesses), keeping other `os.environ` values out of
third-party binaries. The essential set
is `PATH`, `HOME`, locale, temp dirs, the platform's shared-library and
CA-bundle variables, `HTTP(S)_PROXY` / `NO_PROXY`, and `MISE_DATA_DIR` — the
exact list is `ESSENTIAL_ENV` in `src/maina/core/envfilter.py`. A tool that overruns raises `ToolTimeoutError` (a
`MainaError`, not an `InstallError`); a runtime provisioning failure raises
`RuntimeProvisionError` (subclasses `InstallError`).

`requires:` pins and the `npm` backend need [mise](https://mise.jdx.dev/) (the
`pip` backend only when it carries a `python` pin). If
`mise` is not on `PATH`, maina will not run the `mise.run` installer unless you
set `MAINA_ALLOW_MISE_BOOTSTRAP=1`; otherwise it raises `RuntimeProvisionError`.

`extends` parents use registry ids (no `.yaml` suffix), e.g. `extends: kubectl`
(not `kubectl-kubectl`). Family roots resolve from `kubectl/kubectl.yaml` as id
`kubectl`. See `examples/local_tool/` (overlay registry) and
`examples/parsers_dirs/` (custom parser via `parsers_dirs`).

If a `base.yaml` exists in a registry root it auto-extends every tool **in that
same root** — put shared defaults (e.g. org-wide runtime pins) there. It carries
no `cli:` of its own, so `maina list` / `prepare` treat it as inheritance-only;
add `abstract: true` to say so explicitly. A `base.yaml` in another registered
root is *not* applied, so overlay catalogs compose without rewriting each other. maina ships no `base.yaml`, so a static
binary never drags `mise` unless you add pins. The `npm` backend provisions
Node on its own. Pin runtimes where you need them:

- in your own `base.yaml` (per registry root), or
- per tool: `requires: { node: "22" }`
- `requires: null` on a child clears every inherited pin;
  `requires: { node: null }` drops just one

**Quote any string field YAML 1.1 could reinterpret** — `version: "3.10"`
(unquoted it is the float `3.1`), `cli: "yes"` / `install.tag: "on"` (unquoted
they are booleans), and likewise for `install.binary`, `install.assets.*`, and
`env:` values. maina rejects a bare `bool` / `float` in these fields at load
rather than downloading the wrong release, so the mistake is loud either way —
quoting just moves it earlier. A value with no such second reading (`cli: jq`,
`version: 1.7.1`) needs no quotes, and a strict YAML linter will object if you
add them anyway.

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

### Authoring notes

- A manifest's `args` defaults can differ from the upstream CLI so machine
  output is usable by default (e.g. force `--format json`); pass the kwarg to
  override.
- Name an arg `output_format` rather than `output` when it would clash with the
  manifest's top-level `output:` block.
- An intermediate family surface can be callable with empty args on purpose
  (`git remote` lists remotes).
- See `examples/catalog/` for a worked catalog (`github`, `npm`, `pip`,
  `output.from_file`, a subcommand family), `examples/local_tool/` for an
  overlay registry, and `examples/parsers_dirs/` for a custom parser.

## Setup

```bash
uv sync
uv run python examples/demo.py
uv run python examples/parsers_dirs/demo.py
uv run python examples/catalog/demo.py    # worked catalog: github + npm + from_file + family
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
maina validate             # lint every manifest under the registry roots
maina describe jq --json   # a tool's manifest as JSON
maina schema               # the manifest JSON Schema (Draft 2020-12)
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
passed as strings. Top-level YAML `name:` is decoration and ignored; `platforms:`
**is** enforced (see above).

## Security

A registered registry directory is trusted code — it can ship a `parsers/*.py`
that maina imports and runs in-process, and `Runtime.preferred()` honours
`$MAINA_REGISTRY` unless you pass `use_env=False`. See
[docs/security.md](docs/security.md).
