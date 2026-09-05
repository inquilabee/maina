# Changelog

## Unreleased

### Security / correctness

- Registry tool ids can no longer contain `.` / `..` segments; a resolved
  manifest path must stay under a registered root (was: `Tool("-..-x")` could
  load and run a manifest from outside the registry).
- The `mise.run` installer (`curl | sh`) is opt-in: set
  `MAINA_ALLOW_MISE_BOOTSTRAP=1`, else `RuntimeProvisionError`. The bootstrap
  pins `MISE_VERSION`.
- `mise` / `npm` provisioning subprocesses now have timeouts.
- `env_allow` now actually reaches the language-runtime provider:
  `LanguageRuntimeProvider.ensure` / `.env` take a keyword-only `base_env`, and
  the installer hands them the already-trimmed environment, so a secret gated
  out by `env_allow` never reaches a `mise install` / `mise which` subprocess
  (previously they re-read the full `os.environ`).
- `mise`'s runtime-resolution fallback shell-quotes the runtime name
  (`command -v <runtime>`), closing a shell-metacharacter path.
- More of the manifest is validated at load: `requires:` keys must match
  `[A-Za-z0-9_.+-]+`, `install.package` must be a valid npm name (scoped ok),
  and `install.tag` must be a single safe path segment.
- `InstallSpec.identity_key()` (the github / npm cache-dir hash) now also
  covers `install.binary` and `install.allow_scripts`, so flipping either
  cannot reuse the other variant's cached install.
- A `MAINA_REGISTRY` entry that is set but not a directory now emits a
  `RuntimeWarning` instead of vanishing silently.
- The loader survives a repo-shaped registry root: a YAML-boolean key
  (`on:` / `yes:`) is a clean `ManifestError` (was an uncatchable `TypeError`),
  and registry scanning skips dot-directories (`.github/` no longer surfaces
  workflow files as tool ids). One bad file no longer blanks `maina list`.
- `separator:` moved to a top-level key — emitted once before the positional
  block whenever a positional is present (was a per-arg field that vanished
  with its optional arg, letting a `-`-prefixed caller value become an option).
- Fail-early: a scalar `platforms:` and a non-64-hex `install.sha256` are
  rejected at load; an unknown `install.backend` raises before a `requires:`
  runtime is provisioned; `run(timeout=<non-numeric>)` raises `ArgError`.
- `maina prepare --check` consults `requires:` (via the language provider's
  optional `is_present`) — no more "satisfied" for a tool whose runtime is
  missing.
- A `parsers/*.py` that fails to import surfaces on `result.parse_error`
  instead of crashing `run()`; an inherited `parser: *.py` resolves against the
  family of the manifest that declared it.
- `output.from_file`: an arg named `cwd` is rejected at load, `TypeError` is
  caught, and a report older than the run start is flagged stale.
- `args: { <name>: null }` in an overlay drops an inherited arg (mirrors
  `requires: { node: null }`).
- `cli` / `install.binary` also reject a leading `-` and shell-glob
  metacharacters.
- New [Security model](docs/security.md) page: a registry root is trusted
  code; `use_env=False` opts a library out of `$MAINA_REGISTRY`.
- tar extraction requires the data filter (Python 3.11.4+), validates
  symlink/hardlink targets, and wraps `TarError` as `InstallError`; the
  single-file archive fallback only accepts an executable file. Binary copies
  into the cache are atomic (`os.replace`).
- `requires-python` is now `>=3.11.4`.
- `env_allow` matches its essential-var list case-insensitively (Windows
  `SystemRoot` / `Path` no longer dropped) and the list now covers
  `LD_LIBRARY_PATH` / `DYLD_*`, `SSL_CERT_FILE` / `SSL_CERT_DIR` /
  `REQUESTS_CA_BUNDLE`, `HTTP(S)_PROXY` / `NO_PROXY`, and `COMSPEC` / `PATHEXT`.
- `output.from_file` templates may only use bare `{name}` placeholders — a
  `{cwd.__class__…}` / `{a[0]}` placeholder is rejected, and so is a conversion
  or format spec (`{out!r}` / `{out:>1000000000}`, which can drive a huge
  `str.format` allocation). Failures surface on `parse_error`.
- `output.from_file` reports over 64 MiB are refused (`parse_error`) instead of
  read into memory — the last uncapped input path in maina.
- `timeout: 0` or a negative timeout is rejected at manifest load (it used to
  mean "expire immediately"); a non-positive `run(timeout=)` now means "no
  limit".
- A tool's kwargs are validated **before** any install work — a typo'd argument
  no longer pays for a download first.
- `github` archive binary lookup is deterministic (shallowest path, then
  lexicographic) instead of filesystem-iteration order.
- A manifest with an implausible number of YAML anchors is rejected before
  parsing (alias-expansion / "billion laughs" guard).
- `maina run <id> <bare-token>` where the token is not a `type: flag` arg is a
  usage error (`… is not a flag; pass it as token=<value>`) instead of silently
  sending `--token True`.
- `type: pairs` values and keys now go through the same scalar guard as every
  other argv token: `run(env={"DEBUG": False})` raises instead of emitting
  `-e DEBUG=False`, which most CLIs read as *set and truthy*. A dict, list, or
  `None` on either side of a pair is rejected rather than stringified to its
  `repr()`.
- Arg field combinations argv could only half-honour are rejected at load
  instead of silently picking a winner: `join: csv` with `repeat`, `joiner`
  with `repeat: false` (the joiner used to win, so "one flag then all items"
  became a joined flag per item), and `joiner` on a `type: flag` or positional
  arg (never emitted).
- `run(env=...)` validates that names and values are strings, so a port number
  or a `None` is an `ArgError` naming the variable instead of a bare
  `TypeError` raised from inside `subprocess.Popen`.
- A `ManifestError` now names the manifest **file** it came from, and for an
  inherited defect the parents it merged (`… (in .../alpha.yaml, which extends .../base.yaml)`). One typo in a shared `base.yaml` used to report once per
  child and name none of the files. `ManifestError.files` carries the paths.
- `separator:` no longer conflicts with an `args.<name>.order: null` that is
  *clearing* an inherited order — the null-drop idiom resolves the conflict
  rather than causing one.

### CLI

- **`--` passthrough now survives when another app mounts maina's commands.**
  `maina run t -- -n` worked, but the same invocation through an app that added
  maina's Typer app under its own name failed with `'-n' is not a flag`: Click
  drops the separator from `ctx.args`, and only `maina.cli.main()` captured the
  raw argv it was recovered from. The tokens are now also recovered from
  `sys.argv`, accepted only when dropping their first `--` reproduces exactly
  what Click passed in — so a programmatic call (`app(args=[...])`,
  `CliRunner`), where `sys.argv` belongs to the test runner, still falls back.

- `maina --version` / `maina.__version__`.

- Bad manifests / unknown ids print one line and exit 2 (no Rich traceback).

- Non-success tool run exits 1 (2 stays "bad usage").

- `maina run <id> --` runs a no-required-args tool; `key:=value` forces a
  literal string value.

- `maina run <id> --dry-run key=value …` prints the argv that would run
  (argv[0] is the bare `cli` name) and exits without executing.

- `maina run` passes the tool's **stderr through on success** too — a linter's
  warnings and a deprecation notice were previously dropped with no way to get
  them back.

- `maina run --stdin` feeds the process's stdin to the tool, so the CLI can be
  piped into (`cat x.json | maina run jq --stdin filter=.`). Without it the tool
  still gets `/dev/null`, so one that reads stdin never blocks on a terminal.

- `maina run --env-allow=NAME[,NAME...]` (repeatable) — the CLI spelling of
  `run(env_allow=...)`, which `docs/security.md` recommends and which had no CLI
  surface at all.

- `maina run e env.K=V env=plain` now raises the mirror of the error the
  opposite token order already gave, instead of a confusing complaint about the
  pairs shape that never mentioned the conflicting tokens.

- `maina validate` accepts any well-formed `{os}-{arch}` asset key, not only the
  six with an arch alias: an armv7l / ppc64le / s390x / riscv64 catalog could not
  pass its own lint even though the download would succeed. An unnormalised
  spelling (`linux-amd64`) and an OS maina never emits (`darwin-arm64`) are still
  rejected, now with a message saying which to use.

- `maina validate` rejects an `output.from_file` placeholder naming a
  `type: flag` arg — flags are never interpolated, so the template could never
  resolve — and every problem it reports names the manifest file.

- `maina validate [TOOLS...]` loads every manifest (or the named ids) and
  reports each error without aborting — a catalog lint step.

- `maina describe <id> [--json]` prints a tool's manifest as data
  (`Tool.describe()`), so a catalog is consumable from the shell.

- `maina schema` prints a Draft 2020-12 JSON Schema for the manifest,
  generated from the loader's own key sets (`build_manifest_schema()`, also
  published at `docs/maina-manifest.schema.json`). Point an editor at it with a
  `# yaml-language-server: $schema=` header for validation while authoring.

- `maina run` reaches more of `Tool.run`: `--timeout=<s>` / `--cwd=<dir>`
  options, and `arg.subkey=value` tokens that build a `type: pairs` dict
  (`env.K=V env.A=B`).

- `maina clean [TOOLS...]` deletes cached installs; `maina prepare --force`
  wipes a tool's cache before re-preparing it. `Installer.clean_ids()`.
  `--force` now needs explicit ids (a bare `prepare --force` no longer wipes the
  whole tool cache) and is rejected alongside `--check`. A named `maina clean`
  / `--force` deletes only that id's `<cli>/<version>` dir, not every version
  and identity under the `cli`; an abstract / `cli`-less id is rejected.

- `maina validate` goes past "does it load": a concrete manifest must have a
  `cli:` (a `cli`-less one is inheritance-only — mark it `abstract: true`); a
  `github` backend must carry `repo` + `assets`; every `output.from_file`
  placeholder must name a declared arg. These used to surface only at install
  / run time.

- `maina prepare` (no ids, or several) is batch-resilient: one tool that fails
  to install no longer aborts the rest or discards the record of what already
  installed. It prints `prepared: <id>` / `failed: <id>: <message>` and exits
  non-zero if anything failed. `Installer.prepare_ids()` returns
  `(prepared, failures)`.

- `maina run <id>` with no arguments, for a tool that needs none, prints a
  hint to run it as `maina run <id> --`.

- A `base.yaml` with no `cli:` is inheritance-only: `maina list` no longer
  shows a phantom `base`, `maina prepare` skips it instead of dying with
  `manifest 'base' missing cli`, and `maina validate` reports it `ok`.
  `Manifest.has_cli`.

- `examples/catalog/` — a worked catalog covering the `github` and `npm`
  backends, `output.from_file`, and a subcommand family, with an offline
  `demo.py`.

- A `parsers_dirs` module whose stem shadows a built-in parser (`json.py`)
  warns; two `parsers/*.py` files with the same stem get distinct
  `sys.modules` names.

- `prepare --check` on a tool with an unknown `install.backend` now errors
  (a manifest bug) instead of reporting it "missing".

- `run(text=False, stdin="…")` encodes the string rather than raising a bare
  `TypeError`.

- `prepare --check` for a `path`-backed tool provided by a `requires:` runtime
  now searches the PATH a real run would compose (the pinned runtime's bin dir
  plus the ambient PATH), instead of reporting `missing` for a tool `run`
  resolves fine. Still side-effect free: the bin dir comes from a lookup that
  installs nothing (`InstallContext.probe_path`, `MiseRuntime.present_bin_dir`).

- `Registry.refresh()` drops the per-root scan index, so a manifest written into
  an already-registered root becomes visible to `list` / `validate` / `prepare`
  in a long-lived process — and loadable, which for an id only the tree scan can
  resolve (`fam/my-tool.yaml`) it previously was not.

- Parsed manifest YAML is memoized per file against its own mtime and size, so
  repeated `Tool(id)` construction and `maina list` over a large catalog stop
  re-parsing every layer. An edit in place is still picked up with no refresh.

- `output.allow_stale_report: true` opts out of the report-mtime check for an
  incremental tool that leaves an unchanged report alone, or a filesystem whose
  mtime clock is offset from the host's. The 2 s window (previously documented
  only here) is now in the `output` reference, and the error text names the key.

### API

- `ArgError` and `AbstractToolError` are exported from `maina`.
- `ToolTimeoutError` subclasses `MainaError` directly (not `InstallError`).
- `ParseError` / `ToolExecError` added and exported.
- `Tool.manifest` / `Tool.args` (read-only) / `Tool.describe()` (JSON-safe,
  stable) for building your own help / form / CLI.
- `Tool.argv(**kwargs)` returns the argv a run would build (argv[0] is the
  bare `cli` name) — for a dry run, a preview, or driving the subprocess
  yourself.
- `Manifest`, `ArgSpec`, `InstallSpec`, `OutputSpec`, `Registry`, `ToolRunner`
  are re-exported from `maina`.
- `register(..., replace=True)` resets the catalog before adding roots;
  duplicate registry roots are de-duplicated. The lazily-built session Runtime
  closes its HTTP session at process exit.
- `Runtime.preferred(discover=True)` registers every catalog published to the
  `maina.registries` entry-point group — share a catalog as a wheel.

### Tests

- Real `-m integration` tests: a live GitHub download + run, an `npm install`
  - run, and a `mise` `ensure` — each `pytest.skip`-guarded on its
    prerequisite. (Fixed an npm regression they caught: the identity cache key
    was leaking into the npm dist-tag.)

### Robustness

- An asset with an extension maina can't unpack (`.tar.bz2`, `.zst`, `.7z`, ...)
  raises `InstallError` instead of being `chmod +x`'d as-is.
- A `github` archive with no member named `install.binary` is a hard error
  (the message lists the members). The old "run the archive's one executable"
  fallback is now opt-in via `install.allow_any_executable: true` and warns.
- The `github` / `npm` cache-dir hash folds in the `requires:` runtime pins,
  so two manifests that share `cli` + `version` but pin different node
  versions no longer reuse each other's npm build.
- `run(stdin=)` accepts whichever of `str` / `bytes` matches the run's
  text/binary mode; a mismatch is coerced, not a raw `AttributeError`.
- `maina run key=value:=x` — a `:=` after the first `=` is part of the value,
  not the forced-literal separator.
- `env_allow` is materialised once per `run()` — a one-shot generator argument
  no longer applies to the install subprocesses but not the tool.
- `required: true` is not satisfied by `None` — from `run(x=None)` or
  `default: null` — it still raises `ArgError`.
- `ArgError` for an unknown or missing argument now names the tool id and lists
  the known argument names.
- `args.<n>.default` / `.flag` / `.joiner` reject a bare YAML `bool` / `float`
  the way `cli` / `version` / `install.*` already do — `default: no` no longer
  becomes the argv token `"False"`. `args.<n>.repeat` must be a real YAML
  boolean (`repeat: "no"` / `repeat: 0` are rejected, not silently "repeat").
- `install.assets` / `install.sha256` honour `<platform>: null` in a child as a
  drop marker (like `args` / `requires`), so a version-bump child can shed a
  parent's stale asset name or digest.
- A non-scalar argument value (a dict, set, or arbitrary object) is rejected
  with an `ArgError` instead of being `str()`-formatted into argv.
- The unit suite is hermetic against `$MAINA_REGISTRY` / `$MAINA_CACHE`:
  `fixture_runtime()` builds its Runtime with `use_env=False` and a throwaway
  cache root, so an ambient registry can no longer shadow a fixture id and the
  run never touches `~/.cache/maina`.
- `maina list <parent>` finds children when the parent lives in a single
  hyphenated directory (`my-tool/` holding `my-tool.yaml` + `sub.yaml`), not
  only when every hyphen is a directory boundary.
- `extra=` passthrough tokens land just before a `separator:` block (they are
  caller options, not operands) — with `separator: "--"` set, an extra `--stat`
  is no longer forced past the `--` into pathspec position.
- `Runtime.preferred(discover=True)` warns (`RuntimeWarning`) on a
  `maina.registries` entry point that fails to import or does not resolve to a
  directory, instead of dropping it silently; a broken third-party catalog
  module can no longer escape discovery as an arbitrary exception.
- `install.assets` / `install.sha256` deep-merge across `extends` per platform
  key (a child overriding one platform no longer drops the others).
- `abstract` / `args.<n>.required` / `args.<n>.positional` /
  `install.allow_scripts` / `install.allow_any_executable` must be a real YAML
  boolean — a quoted `"true"` or `1` is rejected, not silently coerced (an
  `abstract: "true"` no longer yields a runnable tool).
- `maina validate` also checks that `install.backend` is registered and that
  `install.assets` / `sha256` keys are real `{os}-{arch}` platform keys.
- `cli.py` split — the `maina run` token machinery moved to
  `maina/clitokens.py`.
- A relative `MAINA_CACHE` / `cache_root` is resolved to an absolute path (a
  relative `argv[0]` broke a run with an explicit `cwd`).
- A failed `subprocess` start (bad `cwd`, missing exe) raises `ToolExecError`,
  not a bare `OSError`.
- `Tool.run` defaults stdin to `DEVNULL`; a stdin-reading tool no longer blocks
  on the caller's terminal. Pass `stdin=` to feed it.
- `maina run` / `maina run --dry-run` route a manifest arg whose name collides
  with a `Tool.run` parameter (`parser`, `timeout`, `cwd`, `env`, `stdin`,
  `text`, `extra`, `env_allow`, `args`) through the `args={...}` escape hatch
  automatically. Previously such an arg was silently dropped from argv
  (`parser=`, `text=`, `stdin=`), or crashed `main()` with a raw `TypeError`
  (`timeout=`, `cwd=`, `extra=`, `env=`, `args=`) — and `--dry-run` reported
  success either way, since `Tool.argv()` didn't hit the same collision.
  `examples/catalog/prettier.yaml`'s `parser` arg is now runnable through
  `maina run`.
- `args.<name>.positional: true` together with `flag:` is rejected at load —
  argv only ever emitted the positional, but `help()` / `describe()` reported
  the flag, and a child overriding a parent's positional with `flag:` alone
  (without also setting `positional: false`) loaded clean and silently kept
  emitting a bare operand.
- A non-`flag` arg rejects a `bool` value (`argv(name=False)` no longer emits
  the literal token `"False"`) — matches the existing rejection of a bare
  `default: false` for the same field.
- `env: { VAR: null }` drops that inherited variable (mirrors `args` /
  `requires` / `install.assets`); a plain `VAR:` with no value is a load error
  instead of setting the subprocess variable to the string `"None"`.
- A same-root id collision — two manifest files deriving one id (e.g.
  `kubectl/get.yaml` and `kubectl-get.yaml` both resolving to `kubectl-get`) —
  warns (`RuntimeWarning`) from `Registry.iter_ids()` / `maina list`, and
  `maina validate` reports it as an error naming both files. Previously the
  shadowed file had no diagnostic anywhere.
- `Tool.run(env=...)` raises `ArgError` for a non-mapping `env` (a string, a
  manifest arg literally named `env` passed the wrong way) instead of a bare
  `TypeError` escaping from deep inside the runner.
- `Tool.argv()` accepts the same run-only parameters `Tool.run()` does
  (`parser`, `cwd`, `timeout`, `env_allow`, `env`, `stdin`, `text`) and
  ignores them — so a manifest arg whose name collides with one of them binds
  the same way in both, and a `--dry-run` preview can no longer disagree with
  what `run()` actually sends to argv.
- `maina prepare --check` is batch-resilient: a manifest with a bug (e.g. an
  unknown `install.backend`) no longer aborts the whole readiness report —
  it's reported as its own `error: <id>: ...` line alongside `satisfied:` /
  `missing:` for the rest. `Installer.check_ids()` returns
  `(satisfied, missing, failures)`.
- The published JSON Schema accepts the null-drop idiom (`<key>: null`) in
  `args`, `env`, and `install.assets` / `sha256` — it used to reject exactly
  the inheritance idiom the manifest docs teach on the same page.
  `install.backend` is an open string with the built-in names as non-binding
  `examples`, not a closed enum, since a caller can register a custom
  backend; `args.<name>.choices` items accept an integer as well as a string.
- `maina list <parent>` no longer misses a descendant whose intermediate
  directory has no manifest of its own (`family/foo/bar.yaml` extending the
  family root directly, with no `foo.yaml`) — it used to invent an id for the
  directory, fail to load it, and print a raw Python
  `.../loader.py:N: RuntimeWarning: ...` warning plus an empty listing.
  `list_commands` is also no longer O(N²) on this layout: a per-root
  id→path index is now built once (one `rglob`) and reused by every lookup,
  `iter_ids()`, and `duplicate_ids()`.
- Every `RuntimeWarning` a CLI command can trigger (a skipped broken
  manifest, a same-root id collision) now prints as a plain
  `warning: <message>` line on stderr instead of Python's default
  `.../loader.py:N: RuntimeWarning: ...` dump, which leaked maina's own
  source path into CLI output.
- `Tool.describe()["install"]` carries every `install:` field (`assets`,
  `binary`, `sha256`, `allow_scripts`, `allow_any_executable`), not just
  `backend` / `repo` / `package` / `tag` — a catalog browser can now show
  which platforms a `github` tool has assets for.

### Manifest expressiveness

- **`os.PathLike` values are accepted** for any arg and converted with
  `os.fspath`. A path is the most common thing a wrapped CLI takes and
  `pathlib.Path` is what Python callers already hold, so refusing it (with
  "value must be a string or number, not PosixPath") was friction for no safety
  — `os.fspath` is unambiguous, unlike `str()` on a bool, which is still
  refused. Found by porting a real catalog onto maina.

- **`args.<name>.false_flag`** — the token a `type: flag` arg emits when it is
  explicitly `False` (`--fix` / `--no-fix`). Without it, off emits nothing; with
  it, "not passed" and "passed off" are different requests. Rejected at load on
  any other arg type, where it could never be emitted.

- **`args.<name>.join` is now the separator itself** (`join: ","`,
  `join: ";"`), not the fixed word `csv`. A word where a separator belongs
  (`csv`, `comma`, `semicolon`, ...) is rejected at load naming the punctuation
  it meant — accepting it would silently join the list with that literal text.
  **Breaking:** `join: csv` becomes `join: ","`.

- **`install.asset_template`** with `install.os_map` / `install.arch_map` names
  every platform's github asset from one pattern (`{version}`, `{os}`, `{arch}`)
  instead of six near-identical lines that can drift apart. An explicit `assets`
  entry still wins for its platform. Unknown placeholders and format specs are
  rejected at load; an expansion that is not a safe path segment is refused
  before any download; and the template and maps are part of the cache identity.

- `args.<name>.choices: [...]` limits a value (or every item, for a list) to
  a fixed set — checked on every `run()` / `argv()`, not just at load. Not
  valid on `type: flag` / `type: pairs`. A `default:` that is not itself one
  of the arg's own `choices:` is rejected at load, and a `type: pairs`
  `default:` must be a mapping or a list of `[key, value]` pairs — both used
  to load clean and fail every call.

- `args.<name>.type: pairs` emits one flag per pair — three tokens
  `--flag key value` by default (`jq --arg`), or two (`--flag key=value`) when
  the arg also sets `joiner: "="` (`docker -e K=V`).

- `args.<name>.separator` emits a literal token before an arg (e.g. `--` before
  positional paths).

- `output.from_file: "<template>"` — the parser reads that file instead of
  stdout (for tools that write a report file). `result.raw` stays the tool's
  stdout; the file contents land on the new `result.report`, and
  `result.meta["parsed_from"]` records the path. The template interpolates
  `{cwd}` and any arg by name, including one that only has a manifest
  `default:`. Every failure is surfaced on `result.parse_error` — an
  unresolved placeholder, a path that escapes the run directory, or a missing
  file — never a silent fall back to stdout. Absolute paths and `..` are
  rejected at manifest load.

- Load-time validation extended: `join` must be `csv`, `timeout` must be a
  number, `platforms` values must be `linux` / `macos` / `windows`.

- CLI: `--json` after `--` reaches the tool (was stripped from the whole line).

### Results and parsers

- `parser: json` is now **strict** — malformed JSON raises `ParseError`
  (recorded in `ToolResult.parse_error`, `parsed=None`, the run does not
  crash). New `json-lenient` keeps the old swallow-and-return-text behaviour.
- A configured parser is invoked even when stdout is empty (stderr-only tools
  are parseable).
- `maina run` prints `parse error: …` to stderr when `result.parse_error` is
  set, and exits non-zero when `--json` was asked for but parsing produced
  nothing (previously it printed `null` and exited 0).
- `ToolResult` gains `parse_error`, `cwd`, `exe`, `report` (the
  `output.from_file` contents), `parse_input` (report when set, else `raw`),
  and `to_dict()` (JSON-safe, stable key set).
- `Tool.run(text=False)` / `ToolRunner.run(text=False)` returns the raw
  stdout / stderr bytes in `result.raw_bytes` / `result.stderr_bytes` and skips
  the parser and `output.from_file` — for a tool that writes an archive, image,
  or other binary to stdout. `result.raw` still carries a lossy text view.

## 0.1.0

First tagged release.

### Scope

- maina ships **the engine and the manifest contract, not a tool catalog.**
  `Runtime.preferred()` has an empty registry; register your own manifests with
  `register(rt, registry_dirs=[...])` or point `MAINA_REGISTRY` at them.

### Manifest

- The whole manifest is validated at load: unknown top-level keys, unknown arg
  `type`, a non-positional arg with no `flag`, and an unknown `parser:` name all
  fail closed (they were silently ignored / deferred before).
- `abstract` is no longer inherited — a child of a family root is concrete
  unless it declares `abstract: true` itself.
- `args.<name>.joiner` emits `--flag=value` as one token.
- `args` inheritance merges per field (override `default:`, keep `flag`).
- `cli` / `version` / `install.binary` / every `install.assets` value must be
  single safe path segments, and `install.repo` must be `owner/name`; a manifest
  can no longer write outside the cache root.
- The `github` / `npm` cache key now includes a hash of
  `repo`/`tag`/`assets`/`sha256`/`package`, so a manifest can't reuse another's
  cached binary or bypass a changed digest pin.
- `env_allow` now also trims the environment handed to `npm install` / `mise`;
  `npm install` runs with `--ignore-scripts` unless `install.allow_scripts: true`.
- Optional `schema: 1` top-level key.
- A hyphenated manifest filename in a subdirectory (`family/my-tool.yaml`) and
  `.yml` files now resolve.

### Runtime / CLI

- `Runtime.preferred(cache_root=, language=, registry_dirs=, use_env=)` and
  `Runtime.close()` / context-manager support.
- `Tool.run(stdin=..., args={...})`; undecodable tool output no longer crashes
  (decoded with `errors="replace"`).
- `maina run` prints the tool's stdout by default; `--json` for `result.parsed`.
  `key=1` stays a string unless the arg is `type: flag`.
- Bad CLI usage exits 2 with one line (no traceback); `import maina` works on
  Windows (the `fcntl` lock degrades to a no-op).

### Runtime

- `Tool(name)` loads a manifest and runs it: install/resolve the executable,
  map keyword arguments to argv, run the process, return a structured
  `ToolResult`.
- Composition `Runtime` with `register(rt, registry_dirs=, parsers=, parsers_dirs=, installers=, language=)`.
- `maina.isolated(rt)` for scoped Runtime swaps.
- CLI: `maina run`, `maina list`, `maina prepare` (with `--check`); catalog from
  `MAINA_REGISTRY`.

### Install backends

- **New `pip` backend** (`install.backend: pip`) installs a pinned Python
  package into a **venv of its own** under
  `<cache>/tools/<cli>/<version>-<hash>/pip/` and resolves its console script.
  One venv per tool, never a shared environment: two tools whose dependencies
  disagree cannot break each other, and adding a tool cannot change what an
  existing one resolves.

  - `version:` is required — an unpinned lint tool changes its findings under
    you.
  - `cli:` is the console script, `install.package:` the distribution providing
    it (they differ more often than for npm).
  - Source distributions are refused by default (`--only-binary=:all:`):
    building an sdist runs the package's own `setup.py` with the composed
    environment, the same exposure npm lifecycle scripts carry. Opt in with
    `install.allow_sdist: true` — the pip analogue of `allow_scripts`.
  - The package index is never manifest-settable; a manifest must not be able to
    redirect where code comes from.
  - `requires: {python: "3.13"}` puts one tool on its own interpreter,
    provisioned through mise like any other runtime, while the rest of the
    catalog follows the host. With no pin nothing is provisioned. A pin is never
    satisfied by whatever `python3` is on `PATH` — that would silently build the
    venv on the wrong interpreter — and it is folded into the cache-directory
    hash, so two tools on different interpreters get different venvs.
  - `prepare --check` probes the entry point without installing.

- `path`, `github` (release assets, optional `sha256`), `npm`.

- Language runtimes (Node) provisioned through mise, only when a manifest
  declares `requires:` or the `npm` backend needs Node.

- `platforms:` enforced at resolve; unsupported hosts fail up front.

### Result and safety

- `run(timeout=...)`, `run(env_allow=[...])` (trim inherited env; secrets stay
  out of tools), manifest `env:` applied to the subprocess.
- Errors: `ToolTimeoutError`, `RuntimeProvisionError`,
  `UnsupportedPlatformError`, `ToolExitError` (all under `MainaError`).
