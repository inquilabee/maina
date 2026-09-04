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

Unknown top-level keys are rejected at load (a typo like `outputs:` fails
closed, it is not silently ignored).

| Key | Type | Notes |
| --- | --- | --- |
| `schema` | int | Manifest schema version. Optional; must be `1`. |
| `cli` | string | Required for a concrete tool. The executable name (a single safe path segment). **Quote it** — a bare `bool`/`float` (`cli: yes`, `version: 3.10`) is rejected at load. |
| `version` | string | Drives the cache directory and the default `github` tag (a single safe path segment). Quote it. |
| `subcommand` | string | A fixed segment inserted after `cli`. Collected down an `extends` chain. |
| `extends` | string | Parent registry id. See [inheritance](#inheritance). |
| `abstract` | bool | `true` marks an inheritance-only surface. **Not inherited** — a child is abstract only if it says so itself. |
| `platforms` | list | `linux` / `macos` / `windows`. Enforced at resolve time. |
| `timeout` | number | Default subprocess timeout in seconds (> 0). Overridden by `run(timeout=...)`. |
| `separator` | string | Token emitted once before the positional block when any positional is present (usually `"--"`). |
| `parser` | string | A general parser name or a `*.py` file. See [Parsers](parsers.md). |
| `env` | map | Static `NAME: value` pairs layered onto the subprocess, always forwarded. |
| `args` | map | Keyword-argument to argv schema. See [args](#args). |
| `install` | map | How to obtain the executable. See [install](#install). |
| `requires` | map | Language runtimes to provision. See [requires](#requires). |
| `output` | map | Result interpretation. See [output](#output). |
| `name` | string | Decoration only; ignored. |

### Schema version

`schema:` is optional and, when present, must be `1` — the only version this
release understands. It is a forward-compatibility marker: a future maina that
introduces a `schema: 2` shape will still read a `schema: 1` (or unversioned)
manifest, and this release fails closed on anything numbered higher rather than
guessing. Pin it in a catalog you publish so an older maina gives a clear error
instead of a confusing one.

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
| `type` | string | `string`, `flag`, `path`, or `pairs` (rejected otherwise). `path` is emit-only metadata (no coercion). |
| `flag` | string | The option flag, e.g. `--config`. Required unless `positional: true`. |
| `joiner` | string | Emit `flag` and value as one token: `joiner: "="` → `--config=value`. |
| `order` | int | Sort key for this arg's tokens in argv. Default is options (0) before positionals (100), each in declaration order; set `order:` to interleave — e.g. an option *after* a positional (`find <path> -name '*.py'`). |
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
  (`--flag a --flag b`) unless `repeat: false`. `joiner` applies to each token
  (`--flag=a --flag=b`).
- Passing an argument name not in the schema raises `ArgError`.
- An `args` entry whose name collides with a `run()` parameter (`parser`,
  `timeout`, `cwd`, `stdin`, `env_allow`, `extra`, `args`) is still valid —
  pass it via `run(args={"timeout": "30"})`.
- Child `args` merge onto the parent per field: overriding `default:` keeps the
  inherited `flag` / `type` / `required`.
- By default all options are emitted before all positionals (each in
  declaration order). Give an arg `order: <int>` to place it anywhere — e.g.
  `path: { positional: true, order: 1 }` + `name: { flag: -name, order: 2 }`
  yields `find <path> -name <pat>`.
- `type: pairs` takes a dict or a list of `[key, value]` items and emits one
  flag per pair. Default is three tokens `--flag key value` (`jq --arg name v`).
  Add `joiner:` for the two-token `--flag key<joiner>value` shape
  (`docker -e K=V` uses `joiner: "="`).

```yaml
args:
  arg: { flag: --arg, type: pairs }              # run(arg={"x": "1"}) -> --arg x 1
  env: { flag: -e, type: pairs, joiner: "=" }    # run(env={"K": "V"}) -> -e K=V
```

Set a top-level `separator: "--"` to emit that token once before the positional
block whenever any positional is present, so a caller value that starts with `-`
can't be read as an option:

```yaml
separator: "--"
args:
  paths: { positional: true, repeat: true, type: path }
# run(paths=["-x"]) -> git log -- -x
```

## output

```yaml
output:
  format: json
  success_codes: [0, 1]
  from_file: "{report_dir}/results.json"
```

| Key | Meaning |
| --- | --- |
| `format` | Free-form hint (`json` / `text` / ...); not enforced. |
| `success_codes` | Exit codes counted as `ok`. Default `[0]`. |
| `from_file` | When set, the parser reads **this file** instead of stdout — for tools that write a report file. `result.raw` still holds the tool's stdout; the file contents go on `result.report` and `result.meta["parsed_from"]` records the path. The template interpolates `{cwd}` and any string- or number-valued arg by name (including one that only has a manifest `default:`); list / bool args are not substituted. Placeholders are bare names only — `{cwd.parent}` / `{a[0]}` are rejected. It is resolved relative to `cwd`, a relative path only — absolute paths and `..` are rejected at load. Any failure (unresolved placeholder, a path that escapes the run directory, a missing file) lands on `result.parse_error`; there is no silent fall back to stdout. |

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
| `repo` | `github` | `owner/name` GitHub repository (validated at load). |
| `assets` | `github` | Platform key (`linux-x86_64`, `macos-arm64`, ...) to release asset name (each a single safe path segment). |
| `binary` | `github` | Executable name inside the asset. Defaults to `cli`. |
| `tag` | `github` | Release tag (a single safe path segment). Defaults to `version`; `latest` uses the latest release. |
| `sha256` | `github` | Optional platform key to hex digest. A mismatch aborts before `chmod +x`. |
| `package` | `npm` | npm package name — validated at load (`@scope/name` allowed). Defaults to `cli`. |
| `allow_scripts` | `npm` | `true` lets `npm install` run the package's lifecycle scripts. Default `false` (`--ignore-scripts`). |

`.tar.gz`, `.tar.xz`, `.zip`, `.gz`, `.xz`, and bare-binary assets are all
handled. Archive members that escape the destination are rejected.

The `github` / `npm` cache directory is keyed by `cli` + `version` **and** a
hash of `repo` / `tag` / `assets` / `sha256` / `package` / `binary` /
`allow_scripts`, so two manifests that share `cli` + `version` but fetch or
unpack different things never collide and a changed digest pin forces a fresh
download.

## requires

Language runtimes to provision through mise before the tool runs.

```yaml
requires:
  node: "22"
```

- Keys are runtime names — `[A-Za-z0-9_.+-]+`, validated at load. Values are
  version strings. **Quote pins** — unquoted `3.10` is a YAML float and is
  rejected.
- Inheritance: child pins merge onto the parent's. `requires: null` on a child
  clears every inherited pin; `requires: { node: null }` drops just that one.

maina ships no `base.yaml`. If a registry root has one it auto-extends every
tool in that root — a place for shared runtime pins. Without it, a `path` or
`github` tool never touches mise; the `npm` backend provisions Node on its own.

## inheritance

`extends` builds a chain root to leaf; a manifest with no `extends` still picks
up `base` when a `base.yaml` exists, so the effective chain is
`base -> ... -> tool`. Merge rules:

- `args`, `install`, `env`, `output`, `requires` deep-merge (child wins per
  key). In `args`, `<name>: null` drops an inherited arg entirely (as
  `requires: { node: null }` drops a pin).
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
| `raw` | str | stdout as text (a `text=False` run decodes it lossily). |
| `stderr` | str | stderr as text. |
| `raw_bytes` / `stderr_bytes` | bytes or None | Exact bytes — set only for a `run(text=False)` call, which also skips the parser and `output.from_file`. |
| `command` | list[str] | The argv that ran (argv[0] is the resolved executable). |
| `parsed` | any | Parser output, or `None`. |
| `parse_error` | str or None | Why parsing did not happen or failed (a raising parser, a bad/missing `output.from_file`). `parsed` stays `None`; the run never crashes. |
| `report` | str or None | The `output.from_file` contents when that key is set, else `None`. |
| `cwd` | str or None | The working directory the tool ran in (the `cwd=` passed to `run`). |
| `exe` | str or None | The resolved executable path. |
| `version` | str or None | Manifest `version`. |
| `duration_ms` | float | Wall time. |
| `meta` | dict | Free space for parsers; maina records `parsed_from` / `parsed_from_missing` here for `output.from_file`. |

`result.parse_input` is what the parser sees — `report` when `output.from_file`
is set, otherwise `raw`.

`result.to_dict()` returns a JSON-safe view with a stable key set (a
non-serialisable `parsed` is dropped and noted in `parse_error`).

`result.raise_for_status()` raises `ToolExitError` when `ok` is false and
returns `result` otherwise.
