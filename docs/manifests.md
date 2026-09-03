# Manifest schema

A registry entry is a single YAML file. maina loads it, merges any `extends`
chain, and produces a `Manifest` ready to install and run. Unknown keys inside
`args`, `install`, `output`, and `requires` are rejected; the loader fails
closed.

## File layout and ids

- `registry/jq.yaml` -> id `jq`
- `registry/kubectl/get.yaml` -> id `kubectl-get`
- `registry/kubectl/kubectl.yaml` -> family root id `kubectl`
- `registry/git/remote/add.yaml` -> id `git-remote-add`

Ids are hyphen form everywhere (`Tool("kubectl-get")`, `extends: kubectl`). A
trailing duplicate segment collapses: `kubectl-kubectl` resolves to `kubectl`.

## Top-level keys

| Key | Type | Notes |
| --- | --- | --- |
| `cli` | string | Required for a concrete tool. The executable name (argv[0]). |
| `version` | string | Drives the cache directory and the default `github` tag. |
| `subcommand` | string | A fixed segment inserted after `cli`. Collected down an `extends` chain. |
| `extends` | string | Parent registry id. See [inheritance](#inheritance). |
| `abstract` | bool | `true` marks an inheritance-only surface: it cannot run or be prepared. |
| `platforms` | list | `linux` / `macos` / `windows`. Enforced at resolve time. |
| `timeout` | number | Default subprocess timeout in seconds. Overridden by `run(timeout=...)`. |
| `parser` | string | A general parser name or a `*.py` file. See [Parsers](parsers.md). |
| `env` | map | Static `NAME: value` pairs layered onto the subprocess, always forwarded. |
| `args` | map | Keyword-argument to argv schema. See [args](#args). |
| `install` | map | How to obtain the executable. See [install](#install). |
| `requires` | map | Language runtimes to provision. See [requires](#requires). |
| `output` | map | Result interpretation. See [output](#output). |
| `name` | string | Decoration only; ignored. |

## args

Each entry under `args` maps one keyword argument to argv tokens.

```yaml
args:
  path:
    positional: true
    repeat: true
    type: path
    required: true
    description: File(s) to check
  format:
    flag: -f
    type: string
    default: json
  fix:
    flag: --fix
    type: flag
```

| Field | Type | Meaning |
| --- | --- | --- |
| `type` | string | `string`, `flag`, or `path`. `path` is emit-only metadata (no coercion). |
| `flag` | string | The option flag, e.g. `--config`. Omitted for positionals. |
| `positional` | bool | Emit the value with no flag, after all options. |
| `required` | bool | Missing value with no `default` raises `ArgError`. |
| `default` | any | Value used when the caller omits the argument. |
| `repeat` | bool | For list values: `true` repeats the flag per item, `false` emits one flag then all items. |
| `join` | string | `csv` joins a list into one comma-separated token. |
| `description` / `help` | string | Shown in `Tool(id).help()`. |

Calling rules:

- `flag` type expects a bool. `True` emits the flag; `False` / `None` emit
  nothing.
- A list value with `join: csv` becomes `--flag a,b`. Otherwise it repeats
  (`--flag a --flag b`) unless `repeat: false`.
- Passing an argument name not in the schema raises `ArgError`.

## install

```yaml
install:
  backend: github
  repo: koalaman/shellcheck
  tag: v0.10.0
  binary: shellcheck
  assets:
    linux-x86_64: shellcheck-v0.10.0.linux.x86_64.tar.xz
    macos-arm64: shellcheck-v0.10.0.darwin.aarch64.tar.xz
  sha256:
    linux-x86_64: <hex digest>
```

| Field | Backends | Meaning |
| --- | --- | --- |
| `backend` | all | `path` (default), `github`, `npm`, or a registered custom name. |
| `repo` | `github` | `owner/name` GitHub repository. |
| `assets` | `github` | Platform key (`linux-x86_64`, `macos-arm64`, ...) to release asset name. |
| `binary` | `github` | Executable name inside the asset. Defaults to `cli`. |
| `tag` | `github` | Release tag. Defaults to `version`; `latest` uses the latest release. |
| `sha256` | `github` | Optional platform key to hex digest. A mismatch aborts before `chmod +x`. |
| `package` | `npm` | npm package name. Defaults to `cli`. |

`.tar.gz`, `.tar.xz`, `.zip`, `.gz`, `.xz`, and bare-binary assets are all
handled. Archive members that escape the destination are rejected.

## requires

Language runtimes to provision through mise before the tool runs.

```yaml
requires:
  node: "22"
```

- Keys are runtime names; values are version strings. **Quote pins** — unquoted
  `3.10` is a YAML float and is rejected.
- The legacy singular form `requires: { runtime: node, version: "20" }` is still
  accepted, but not mixed with freeform keys.
- Inheritance: child pins merge onto the parent's. `requires: null` on a child
  clears every inherited pin; `requires: { node: null }` drops just that one.

maina ships no `base.yaml`. If a registry root has one it auto-extends every
tool in that root — a place for shared runtime pins. Without it, a `path` or
`github` tool never touches mise; the `npm` backend provisions Node on its own.

## output

```yaml
output:
  format: text
  success_codes: [0, 1]
```

| Field | Meaning |
| --- | --- |
| `format` | Free-text hint (`text`, `json`, ...). Informational. |
| `success_codes` | Exit codes counted as success. Default `[0]`. Sets `ToolResult.ok`. |

## inheritance

`extends` builds a chain root to leaf; a manifest with no `extends` still picks
up `base` when a `base.yaml` exists, so the effective chain is
`base -> ... -> tool`. Merge rules:

- `args`, `install`, `env`, `output`, `requires` deep-merge (child wins per
  key).
- Every other key is replaced by the child's value.
- `subcommand` segments accumulate down the chain into `Manifest.subcommands`.
- A cycle in `extends` raises `ManifestError`.

## ToolResult

`run()` returns a frozen-ish dataclass:

| Field | Type | Meaning |
| --- | --- | --- |
| `tool` | str | Resolved tool id. |
| `ok` | bool | `exit_code in success_codes`. |
| `exit_code` | int | Process return code. |
| `raw` | str | stdout. |
| `stderr` | str | stderr. |
| `parsed` | any | Parser output, or `None`. |
| `command` | list[str] | The argv that ran. |
| `version` | str or None | Manifest `version`. |
| `duration_ms` | float | Wall time. |
| `meta` | dict | Free space for parsers. |

`result.raise_for_status()` raises `ToolExitError` when `ok` is false and
returns `result` otherwise.
