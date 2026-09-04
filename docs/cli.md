# CLI

The `maina` command is a thin front over `Tool`. The manifest stays the source
of truth — there is no per-tool wrapper command.

## Catalog

maina ships no manifests, so the CLI needs a catalog to act on. Set
`MAINA_REGISTRY` to one or more manifest directories (`os.pathsep`-separated):

```bash
export MAINA_REGISTRY=./my_tools:./vendor_tools
maina list
```

Library callers use `register(rt, registry_dirs=[...])` instead.

## `maina list [PARENT]`

List callable tool ids in the registered catalog. With no argument, every
concrete id is printed (including nested hyphen ids such as `kubectl-get`). With
a parent id, every id nested under it (at any depth) is listed — even one whose
intermediate segment has no manifest of its own, e.g. `family/foo/bar.yaml`
extending the family root directly shows up under `maina list family` with no
`foo.yaml` required. Abstract parents never appear in the listing but may be
passed as `PARENT`.

```bash
maina list
maina list kubectl
```

## `maina run <tool_id> [key=value ...] [-- extra...]`

Call `Tool(tool_id).run(**kwargs)`.

- Each `key=value` token becomes a keyword argument. Repeat a key for a list
  value.
- A bare `key` sets that flag to `true`.
- `true` / `yes` / `1` / `0` in a `key=value` are coerced to booleans **only for
  args the manifest declares as `type: flag`**; every other value stays a
  string. `key:=value` always forces the literal string.
- `arg.subkey=value` builds a `type: pairs` dict — `env.K=V env.A=B` →
  `{"env": {"K": "V", "A": "B"}}`.
- Tokens after `--` are passed through as `extra` argv.
- `--timeout=<seconds>` and `--cwd=<dir>` (placed before `--`) map to the
  matching `Tool.run` parameters.
- `--json` prints `result.parsed` as JSON; without it the CLI prints the tool's
  stdout exactly as produced. A parser failure is printed to stderr as
  `parse error: …`; under `--json` it also makes the command exit non-zero.
- `--dry-run` prints the argv that would run (argv[0] is the bare `cli` name; a
  real run resolves it to the cached executable) and exits without running it.

```bash
maina run jq filter=".items[].name" path=data.json raw
maina run rg pattern=TODO path=src path=tests glob='*.py'
maina run jscpd path=src -- --silent
maina run kubectl-get resource=pods --json
```

`maina run <tool_id>` with no tokens, or `maina run <tool_id> --help`, prints
that tool's manifest help. To run a tool that has **no required arguments**,
pass an explicit empty separator: `maina run <tool_id> --`.

Exit codes: `0` on success, `1` when the tool ran but reported a non-success
code, `2` for an unknown id, a bad manifest, or bad arguments.

`maina --version` prints the installed version.

## `maina prepare [TOOLS...] [--check]`

Ensure mise runtimes and tool installs.

- No arguments: prepare every concrete tool. Tools unsupported on this host
  (`platforms:`) are skipped.
- Explicit ids: prepare exactly those. An abstract id fails with exit `2`.
- `--check`: install nothing. Print `satisfied: <id>` or `missing: <id>` for
  each tool and exit non-zero if anything is missing. Side-effect free — no
  download, no mise call. A manifest bug (e.g. an unknown `install.backend`)
  is its own `error: <id>: ...` line rather than aborting the whole report.
- `--force`: delete the named tools' cached installs before preparing. It needs
  explicit ids and cannot be combined with `--check`. To wipe the whole tool
  cache, use `maina clean`.

```bash
maina prepare
maina prepare shellcheck hadolint
maina prepare --check          # CI: "is the toolchain ready?"
maina prepare --force shellcheck   # wipe its cache, then re-download
```

## `maina clean [TOOLS...]`

Delete cached tool installs so the next run re-downloads them. With no
arguments the whole `<cache>/tools/` tree goes; a named id deletes only its own
`<cli>/<version>` directory, so another manifest that shares the `cli` (a
different `version:`, or a distinct `github` / `npm` identity) is left intact.
A `path`-backed tool caches nothing, so it is a no-op. An abstract or
`cli`-less id is rejected.

```bash
maina clean                    # everything
maina clean shellcheck jq
```

## `maina describe <tool_id> [--json]`

Print a tool's manifest as data — for building docs, a form, or shell
completion from a catalog without writing Python. `--json` emits exactly
`Tool(id).describe()`; without it, a human-readable summary of the same
fields.

```bash
maina describe jq --json | jq '.args'
```

## `maina schema`

Print the manifest JSON Schema (Draft 2020-12), generated from the loader's own
key sets. Save it next to a catalog and point an editor at it:

```bash
maina schema > maina-manifest.schema.json
# then, as the first line of a manifest:
# yaml-language-server: $schema=./maina-manifest.schema.json
```

It catches unknown keys, wrong scalar types, and bad enums before `Tool(id)`
runs. Cross-field and backend-specific rules stay with the loader.

## `maina validate [TOOLS...]`

Load every manifest under the registry roots (or just the named ids) and
report each error without stopping at the first. Prints `ok: <id>` /
`error: <id>: <message>` and exits non-zero if any manifest failed — a lint
step for a catalog.

Beyond "does it load", it checks the things that otherwise only fail at
install / run time:

- a concrete manifest has a `cli:` (a `cli`-less one is inheritance-only —
  give it `abstract: true`);
- `install.backend` is registered, and a `github` backend has both `repo:`
  and `assets:`;
- `install.assets` / `sha256` keys are real `{os}-{arch}` platform keys;
- every `output.from_file` placeholder names a declared arg (or `cwd`).

```bash
maina validate                 # CI: "is every manifest well-formed?"
maina validate kubectl-get
```

## `maina` / `maina help`

Print package help.
