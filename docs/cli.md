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
a parent id, only its children are listed. Abstract parents never appear in the
listing but may be passed as `PARENT`.

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
- Tokens after `--` are passed through as `extra` argv.
- `--json` prints `result.parsed` as JSON; without it the CLI prints the tool's
  stdout exactly as produced.

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
  download, no mise call.

```bash
maina prepare
maina prepare shellcheck hadolint
maina prepare --check          # CI: "is the toolchain ready?"
```

## `maina` / `maina help`

Print package help.
