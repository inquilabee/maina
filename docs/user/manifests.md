# Manifest schema

A registry entry is a single YAML file. maina loads it, merges any `extends`
chain, and produces a `Manifest` ready to install and run. Unknown keys inside
`args`, `install`, `output`, and `requires` are rejected; the loader fails
closed.

## JSON Schema

`maina schema` prints a Draft 2020-12 JSON Schema for the manifest shape,
generated from the same key sets the loader validates against. The published
copy is [`maina-manifest.schema.json`](../maina-manifest.schema.json). Point an
editor at it with a header line:

```yaml
# yaml-language-server: $schema=https://inquilabee.github.io/maina/maina-manifest.schema.json
```

It is a *shape* check — unknown keys, wrong scalar types, bad enums. Rules it
can't express are caught elsewhere: `separator` vs `args.order` and the scalar
guards at manifest load (`Tool(id)`), and `github` needing `repo` + `assets`, a
`from_file` template naming a real arg, and unknown asset platform keys by
[`maina validate`](cli.md#maina-validate-tools).

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
| `type` | string | `string`, `flag`, `path`, or `pairs` (rejected otherwise). `path` is emit-only metadata for help and docs; it does not change emission. A `pathlib.Path` (any `os.PathLike`) is accepted for *any* arg and converted with `os.fspath`. |
| `flag` | string | The option flag, e.g. `--config`. Required unless `positional: true`. Mutually exclusive with `positional: true` — set both and the manifest fails to load. |
| `false_flag` | string | Token emitted when a `type: flag` arg is explicitly `False` (`--no-fix`). Without it, off emits nothing — with it, "not passed" and "passed off" are different requests. |
| `joiner` | string | Emit `flag` and value as one token: `joiner: "="` → `--config=value`. |
| `order` | int | Sort key for this arg's tokens in argv. Default is options (0) before positionals (100), each in declaration order; set `order:` to interleave — e.g. an option *after* a positional (`find <path> -name '*.py'`). |
| `positional` | bool | Emit the value with no flag, after all options. A child that gives a parent's positional arg a `flag:` must also set `positional: false` — arg fields merge per-field down `extends`, so `positional: true` otherwise stays set and the load fails. |
| `required` | bool | Missing value with no `default` raises `ArgError`. |
| `default` | any | Value used when the caller omits the argument. For `type: pairs` it must be a mapping or a list of `[key, value]` pairs; if `choices:` is also set, the default must be one of them — both checked at load. |
| `repeat` | bool | For list values: `true` repeats the flag per item, `false` emits one flag then all items. |
| `join` | string | Separator that joins a list into one token — `join: ","` → `--flag a,b`, `join: ";"` → `--flag 'a;b'`. It is the separator itself, so a word (`csv`, `comma`) is rejected with the punctuation it meant. |
| `choices` | list of string or integer | A non-empty allowlist; the value (or, for a list, every item) must match one, checked at every `run` / `argv`. Not valid on `type: flag` / `type: pairs`. |
| `description` / `help` | string | Shown in `Tool(id).help()`. |

Calling rules:

- `flag` type expects a bool. `True` emits the flag; `False` emits `false_flag`
  when the manifest sets one, else nothing; `None` (unset) emits nothing.
- A list value with `join: ","` becomes `--flag a,b`. Otherwise it repeats
  (`--flag a --flag b`) unless `repeat: false`. `joiner` applies to each token
  (`--flag=a --flag=b`).
- Passing an argument name not in the schema raises `ArgError`.
- An `args` entry whose name collides with a `run()` parameter — `parser`,
  `timeout`, `cwd`, `stdin`, `env_allow`, `env`, `text`, `extra`, `args` — is
  still valid, but from Python pass it via `run(args={"env": "browser"})`, not
  `run(env="browser")` (which sets the subprocess environment instead).
  `maina run` on the CLI routes a `key=value` token with one of these names
  through that same escape hatch automatically, so `maina run tool env=browser` reaches the manifest's `env` arg rather than the subprocess
  environment.
- Child `args` merge onto the parent per field: overriding `default:` keeps the
  inherited `flag` / `type` / `required`.
- By default all options are emitted before all positionals (each in
  declaration order). Give an arg `order: <int>` to place it anywhere — e.g.
  `path: { positional: true, order: 1 }` + `name: { flag: -name, order: 2 }`
  yields `find <path> -name <pat>`. `order:` and the top-level `separator:` are
  two ways to lay out argv and cannot be combined (rejected at load).
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

`extra=` passthrough tokens are emitted just *before* that separator (they are
caller-supplied options, not operands) — `run(paths=["a"], extra=["--stat"])`
with `separator: "--"` yields `git log --stat -- a`, not `git log -- a --stat`.

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
| `from_file` | When set, the parser reads **this file** instead of stdout — for tools that write a report file. `result.raw` still holds the tool's stdout; the file contents go on `result.report` and `result.meta["parsed_from"]` records the path. The template interpolates `{cwd}` and any string- or number-valued arg by name (including one that only has a manifest `default:`); list / bool args are not substituted. Placeholders are bare names only — `{cwd.parent}` / `{a[0]}` and a conversion / format spec (`{out!r}` / `{out:>10}`) are rejected. It is resolved relative to `cwd`, a relative path only — absolute paths and `..` are rejected at load. The file is read up to 64 MiB. Any failure (unresolved placeholder, a path that escapes the run directory, a missing or over-cap file) lands on `result.parse_error`; there is no silent fall back to stdout. A report whose mtime predates the run by more than 2 s is rejected as a leftover from a previous run (see `allow_stale_report`). A placeholder naming a `type: flag` arg is rejected by `maina validate` — a flag is never substituted, so the template could never resolve. |
| `allow_stale_report` | `true` accepts an `output.from_file` report older than the run that should have written it. Default `false`, so a tool that failed to rewrite its report is never parsed as this run's output. Turn it on for an incremental or content-hashing tool that deliberately leaves an unchanged report alone, or on a filesystem whose mtime clock is offset from the host's (NFS, some container bind mounts). |

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
| `backend` | all | `path` (default), `github`, `npm`, `pip`, `script`, or a registered custom name. |
| `repo` | `github` | `owner/name` GitHub repository (validated at load). |
| `assets` | `github` | Platform key (`linux-x86_64`, `macos-arm64`, ...) to release asset name (each a single safe path segment). An entry here overrides `asset_template` for that platform. |
| `asset_template` | `github` | One pattern for every platform's asset name, instead of repeating six near-identical lines. Placeholders are `{version}`, `{os}`, `{arch}` — anything else is rejected at load rather than surfacing as a 404 mid-install. |
| `os_map` / `arch_map` | `github` | maina's token to the project's spelling, applied before `asset_template` expands: `arch_map: {x86_64: x64}` turns `linux-x86_64` into `linux_x64`. `{os}` is maina's token (`linux` / `macos` / `windows`), so a project that names its macOS asset `darwin` needs `os_map: {macos: darwin}`. Values must be safe path segments. |
| `binary` | `github` | Executable name inside the asset. Defaults to `cli`. `npm` ignores it and always links `node_modules/.bin/<cli>`, so for an npm package whose bin name differs from the package name, `cli:` must be the **bin** name and `package:` the package name. |
| `tag` | `github` | Release tag (a single safe path segment). Defaults to `version`. `latest` resolves the newest release **once**: the download is cached under that tag, so later runs keep using it until `maina clean <id>`. Pin a real tag for a reproducible catalog. |
| `sha256` | `github` | Optional platform key to hex digest. A mismatch aborts before `chmod +x`. |
| `package` | `npm`, `pip` | Package name to install. For `npm`, validated at load (`@scope/name` allowed). For `pip`, the distribution that provides the `cli:` console script. Defaults to `cli`. |
| `entry` | `script` | File or directory to run, resolved at load time: a bare name under the nearest family `scripts/` dir, a `/`-containing path under the registry root. See [install backends](backends.md#script). |
| `allow_scripts` | `npm` | `true` lets `npm install` run the package's lifecycle scripts. Default `false` (`--ignore-scripts`). |
| `allow_sdist` | `pip` | `true` lets pip build a source distribution, which executes the package's own `setup.py`. Default `false` (`--only-binary=:all:`) — the pip analogue of `allow_scripts`. |
| `allow_any_executable` | `github` | `true` lets extraction fall back to the archive's single executable member when no member is named `binary`. Default `false` — otherwise a missing named member is a hard error, so maina never runs a binary the manifest did not name. |

`.tar.gz`, `.tar.xz`, `.zip`, `.gz`, `.xz`, and bare-binary assets are all
handled. Archive members that escape the destination are rejected.

The `github` / `npm` / `pip` cache directory is keyed by `cli` + `version` **and** a
hash of `repo` / `tag` / `assets` / `sha256` / `package` / `binary` /
`allow_scripts` / `allow_sdist` / `allow_any_executable` **and the `requires:`
runtime pins**,
so two manifests that share `cli` + `version` but fetch or unpack different
things never collide and a changed digest pin forces a fresh download. The
runtime pins are part of the key because an npm package is built against the
Node that installed it and a pip venv is built by one interpreter — the cost is that bumping an unrelated pin also forces
a re-download for a `github` tool.

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
tool **in that same root** — a place for shared runtime pins. A `base.yaml` in
another registered root does not leak in, so composing overlay catalogs is
safe. Without a `base.yaml`, a `path` or `github` tool never touches mise; the
`npm` backend provisions Node on its own.

## inheritance

`extends` builds a chain root to leaf; a manifest with no `extends` still picks
up the `base` **from its own root** when that root has a `base.yaml`, so the
effective chain is `base -> ... -> tool`. A `base.yaml` normally has no `cli:` —
it is inheritance-only, and `maina list` / `prepare` / `validate` skip it on
that basis; give it `abstract: true` to make the intent explicit. Merge rules:

- `args`, `install`, `env`, `output`, `requires` merge per key (child wins). In
  `args`, `<name>: null` drops an inherited arg entirely (as
  `requires: { node: null }` drops a pin). `install` merges one level, but its
  `assets` and `sha256` sub-maps merge per platform key — a child overriding one
  platform's asset keeps the parent's other platforms, and
  `assets: { macos-arm64: null }` drops just that inherited entry (use it on a
  version-bump child so a parent's stale asset name / digest does not linger).
- A bare `args: null` / `install: null` / `env: null` / `output: null` clears
  the *whole* inherited block, not just one key — the same all-or-nothing
  idiom `requires: null` already gets, on all five merged blocks.
- Every other key is replaced by the child's value.
- `subcommand` segments accumulate down the chain into `Manifest.subcommands`.
- A cycle in `extends` raises `ManifestError`.

## Escape hatches and limits

The argv model covers options, positionals, csv joins, repeat-vs-single lists,
`joiner`, `pairs`, `order:` interleaving, `separator`, and `extra=` passthrough.
Read this before writing a manifest for an awkward CLI — it is the five-minute
answer to "does my tool fit".

What a manifest **cannot** express, and the way out of each:

| Not expressible | Do this instead |
| --- | --- |
| Mutually exclusive or co-required args (`--a` xor `--b`) | Let the tool reject the combination, or wrap the call in your own function. |
| Conditional emission (emit `--b` only when `a` is set) | Two manifests via `extends`, or pick the kwargs in Python. |
| Counted flags (`-vvv`) | `extra=["-vvv"]`. |
| A subcommand chosen at run time | One manifest per subcommand (`kubectl/get.yaml`, `kubectl/apply.yaml`) — they share a parent, so the duplication is one line each. |
| Interpolating an arg into `env:` (a secret via the environment) | `run(env={"TOKEN": ...})` — `env:` in YAML is static by design, so a manifest cannot exfiltrate a caller's value into a variable. |
| Collapsing a list of paths to one directory (or a project root) | Compute the single value before calling. maina has no notion of a project root, and "many paths means scan from the root" is a policy the caller owns, not an argv shape. |
| An npm bin whose name differs from the package | `cli:` is the bin name, `package:` the package name (see [install](#install)). |

Two hatches worth knowing:

- **`extra=`** appends raw argv tokens, so anything the schema cannot say is
  still one call away: `run(path="src", extra=["--unstable-flag"])`.
- **`args={...}`** passes a manifest arg whose name collides with one of
  `run()`'s own parameters (`timeout`, `cwd`, `env`, `parser`, `stdin`, `text`,
  `extra`, `env_allow`, `args`) — see [reserved names](#args).

## Authoring notes

- **Quote any string YAML 1.1 could reinterpret.** `version: "3.10"` (unquoted it
  is the float `3.1`), `cli: "yes"` / `install.tag: "on"` (unquoted they are
  booleans), and likewise `install.binary`, `install.assets.*`, and `env:` values.
  maina rejects a bare `bool` / `float` in these fields at load rather than
  downloading the wrong release, so the mistake is loud either way. A value with
  no second reading (`cli: jq`, `version: 1.7.1`) needs no quotes, and a strict
  YAML linter will object if you add them.
- **Defaults may differ from the upstream CLI** so machine output is usable by
  default (for example force `--format json`); pass the kwarg to override.
- **Name an arg `output_format`, not `output`,** when it would clash with the
  manifest's top-level `output:` block.
- **An intermediate family surface can be callable with empty args on purpose**
  (`git remote` lists remotes).
- **`name:` at the top level is decoration** and is ignored; `platforms:` *is*
  enforced (an unsupported host raises `UnsupportedPlatformError`, and
  `maina prepare` with no ids skips that tool).
- **`base.yaml`** in a registry root auto-extends every tool in that same root, so
  shared defaults (for example runtime pins) live there. It has no `cli:` of its
  own; add `abstract: true` to say so explicitly. A `base.yaml` in another
  registered root is *not* applied, so overlay catalogs compose without rewriting
  each other.

## ToolResult

`run()` returns a frozen-ish dataclass:

| Field | Type | Meaning |
| --- | --- | --- |
| `tool` | str | Resolved tool id. |
| `ok` | bool | `exit_code in success_codes`. Independent of `parse_error`: a run can be `ok` with a failed parse, so check both when you rely on `parsed`. |
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
