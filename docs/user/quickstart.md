# Quickstart

## Install

```bash
pip install maina      # or: uv add maina
```

maina needs Python **3.11.4** or newer (older 3.11 patch levels lack the
`tarfile` data filter and are rejected for github installs). A language runtime
is pulled through [mise](https://mise.jdx.dev/) only for `requires:` pins and the
`npm` backend; if `mise` is not on `PATH`, set `MAINA_ALLOW_MISE_BOOTSTRAP=1` to
let maina run the `mise.run` installer (`curl | sh`) or install mise yourself.

## Write a manifest

maina ships no tool manifests — the catalog is yours, and it is the whole
interface. One file per tool, named after its id. `my_tools/shellcheck.yaml`:

```yaml
cli: shellcheck
version: 0.10.0
install:
  backend: github
  repo: koalaman/shellcheck
  tag: v0.10.0
  assets:
    linux-x86_64: shellcheck-v0.10.0.linux.x86_64.tar.xz
    macos-arm64: shellcheck-v0.10.0.darwin.aarch64.tar.xz
parser: json
args:
  path: {positional: true, required: true, repeat: true, type: path}
  format: {flag: -f, default: json}
output:
  success_codes: [0, 1]   # 1 = findings; still a successful run
```

That is the whole contract: `cli` is the executable, `install` says where it
comes from (`backend: path`, the default, uses whatever is already on `PATH` and
downloads nothing), `args` maps kwargs to argv, and `parser` turns stdout into
data. The [manifest reference](manifests.md) has every key; `maina schema`
emits a JSON Schema your editor can check the file against as you type, and
`maina validate` lints the whole catalog.

## Register a catalog

Point a `Runtime` at the directory
([layout](manifests.md#file-layout-and-ids)); `register(...)` returns a
`MainaApp` bound to it:

```python
from pathlib import Path
import maina

app = maina.register(maina.Runtime.preferred(), registry_dirs=[Path("my_tools")])
```

Or mutate the process session (`maina.register(registry_dirs=[...])`) and call
`maina.Tool(name)` directly instead of `app.tool(name)` below.

## Call a tool

```python
result = app.tool("shellcheck").run(path=["scripts/deploy.sh"], format="json")
result.raise_for_status()          # raises ToolExitError on a non-success code
print(result.parsed)               # json parser -> Python list/dict
```

The call form is equivalent to `.run(...)`:

```python
app.tool("jq")(filter=".", path="data.json")
```

`app.tool(name)` is `Tool(name, runtime=app.runtime)` — reach for the explicit
form directly when you need it (e.g. passing a `Runtime` you built some other
way).

## Read the result

`run()` returns a [`ToolResult`](manifests.md#toolresult):

| Field | Meaning |
| --- | --- |
| `raw` | stdout as text |
| `parsed` | parser output, or `None` |
| `ok` | `exit_code` is in the manifest's `success_codes` |
| `parse_error` | why parsing failed, or `None` — a run can be `ok` with a failed parse |
| `exit_code` | process return code |
| `stderr` | stderr as text |
| `command` | the argv list that ran |
| `duration_ms` | wall time |

## Options on every call

These assume an `rg` manifest in your catalog:

```python
app.tool("rg").run(
    pattern="TODO",
    path=["src"],
    timeout=30,                 # seconds; ToolTimeoutError on overrun
    env_allow=["RIPGREP_CONFIG_PATH"],  # trim env; secrets stay out of the tool
    env={"RIPGREP_CONFIG_PATH": "/repo/.rgrc"},  # extra per-call variables
    cwd="/repo",
    text=False,                 # bytes in result.raw_bytes; skips the parser
)
```

With `env_allow` set, only essential variables (`PATH`, locale, temp,
shared-library / CA-bundle / proxy vars, `MISE_DATA_DIR`) plus the names you
list are forwarded to the subprocess. `env={...}` layers on top of that; the
manifest's own `env:` block still wins a key collision.

## The CLI

The CLI reads its catalog from `MAINA_REGISTRY` (directories separated by
`os.pathsep`), so point it at your manifests first. With a `my_tools/jq.yaml`
that wraps the `jq` already on your `PATH`:

```yaml
cli: jq
install: {backend: path}
args:
  filter: {positional: true, required: true}
  path: {positional: true, type: path}
  null_input: {flag: -n, type: flag}
```

```bash
export MAINA_REGISTRY=my_tools
maina list                     # tool ids in the catalog
maina run jq filter=. null_input
maina run shellcheck --help    # manifest help for that tool
maina prepare                  # install every registered tool + runtime
maina prepare jq shellcheck    # just these
maina prepare --check          # report state; install nothing
```

With no `MAINA_REGISTRY` the catalog is empty and `maina run` says so. (The CLI
operates on the process session's catalog — see [CLI](cli.md#catalog).)

See [CLI](cli.md) for the full surface.
