# Quickstart

## Install

```bash
pip install maina      # or: uv add maina
```

maina needs Python 3.11 or newer. `curl` and `sh` are required only if a tool
pulls a language runtime through [mise](https://mise.jdx.dev/) and mise is not
already on `PATH`.

## Call a tool

```python
from maina import Tool

result = Tool("shellcheck").run(path=["scripts/deploy.sh"], format="json")
result.raise_for_status()          # raises ToolExitError on a non-success code
print(result.parsed)               # json parser -> Python list/dict
```

The call form is equivalent to `.run(...)`:

```python
Tool("jq")(filter=".", path="data.json")
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
    cwd="/repo",
)
```

With `env_allow` set, only essential variables (`PATH`, locale, temp,
`MISE_DATA_DIR`) plus the names you list are forwarded to the subprocess.

## The CLI

```bash
maina list                     # concrete tool ids
maina run jq filter=. null_input
maina run shellcheck --help    # registry help for that tool
maina prepare                  # install every tool + runtime up front
maina prepare jq shellcheck    # just these
maina prepare --check          # report state; install nothing
```

See [CLI](cli.md) for the full surface.
