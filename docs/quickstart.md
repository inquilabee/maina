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

## Register a catalog

maina ships no tool manifests. Point a `Runtime` at a directory of manifest
YAML ([layout](manifests.md#file-layout-and-ids)):

```python
from pathlib import Path
import maina

rt = maina.Runtime.preferred()
maina.register(rt, registry_dirs=[Path("my_tools")])
```

Or mutate the process session (`maina.register(registry_dirs=[...])`) and drop
the `runtime=` argument below.

## Call a tool

```python
from maina import Tool

result = Tool("shellcheck", runtime=rt).run(path=["scripts/deploy.sh"], format="json")
result.raise_for_status()          # raises ToolExitError on a non-success code
print(result.parsed)               # json parser -> Python list/dict
```

The call form is equivalent to `.run(...)`:

```python
Tool("jq", runtime=rt)(filter=".", path="data.json")
```

## Read the result

`run()` returns a [`ToolResult`](manifests.md#toolresult):

| Field | Meaning |
| --- | --- |
| `raw` | stdout as text |
| `parsed` | parser output, or `None` |
| `ok` | `exit_code` is in the manifest's `success_codes` |
| `exit_code` | process return code |
| `stderr` | stderr as text |
| `command` | the argv list that ran |
| `duration_ms` | wall time |

## Options on every call

```python
Tool("rg").run(
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

```bash
maina list                     # tool ids in the active/registered catalog
maina run jq filter=. null_input
maina run shellcheck --help    # manifest help for that tool
maina prepare                  # install every registered tool + runtime
maina prepare jq shellcheck    # just these
maina prepare --check          # report state; install nothing
```

(The CLI operates on the process session's catalog — see
[CLI](cli.md#catalog).)

See [CLI](cli.md) for the full surface.
