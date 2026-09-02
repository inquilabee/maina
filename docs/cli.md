# CLI

The `maina` command is a thin front over `Tool`. The registry stays the source
of truth — there is no per-tool wrapper command.

## `maina list [PARENT]`

List callable tool ids. With no argument, every concrete id is printed
(including nested hyphen ids such as `kubectl-get`). With a parent id, only its
children are listed. Abstract parents (`base`, `kubectl`, `git`) never appear in
the catalog but may be passed as `PARENT`.

```bash
maina list
maina list kubectl
```

## `maina run <tool_id> [key=value ...] [-- extra...]`

Call `Tool(tool_id).run(**kwargs)`.

- Each `key=value` token becomes a keyword argument. Repeat a key for a list
  value.
- A bare `key` sets that flag to `true`.
- `true` / `yes` / `1` and `false` / `no` / `0` are coerced to booleans; other
  values stay strings.
- Tokens after `--` are passed through as `extra` argv.

```bash
maina run jq filter=".items[].name" path=data.json raw
maina run rg pattern=TODO path=src path=tests glob='*.py'
maina run jscpd path=src -- --silent
```

`maina run <tool_id>` with no tokens, or `maina run <tool_id> --help`, prints
that tool's registry help instead of running it.

Exit codes: `0` on success, `1` when the tool ran but reported a non-success
code, `2` for an unknown id or bad arguments.

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
