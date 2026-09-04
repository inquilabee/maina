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
  `{cwd.__class__…}` / `{a[0]}` placeholder is rejected (surfaces on
  `parse_error`).
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

### CLI

- `maina --version` / `maina.__version__`.
- Bad manifests / unknown ids print one line and exit 2 (no Rich traceback).
- Non-success tool run exits 1 (2 stays "bad usage").
- `maina run <id> --` runs a no-required-args tool; `key:=value` forces a
  literal string value.
- `maina run <id> --dry-run key=value …` prints the argv that would run
  (argv[0] is the bare `cli` name) and exits without executing.
- `maina validate [TOOLS...]` loads every manifest (or the named ids) and
  reports each error without aborting — a catalog lint step.
- `maina describe <id> [--json]` prints a tool's manifest as data
  (`Tool.describe()`), so a catalog is consumable from the shell.
- `maina run` reaches more of `Tool.run`: `--timeout=<s>` / `--cwd=<dir>`
  options, and `arg.subkey=value` tokens that build a `type: pairs` dict
  (`env.K=V env.A=B`).
- `maina clean [TOOLS...]` deletes cached installs; `maina prepare --force`
  wipes a tool's cache before re-preparing it. `Installer.clean_ids()`.
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

### Manifest expressiveness

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

- `path`, `github` (release assets, optional `sha256`), `npm`.
- Language runtimes (Node) provisioned through mise, only when a manifest
  declares `requires:` or the `npm` backend needs Node.
- `platforms:` enforced at resolve; unsupported hosts fail up front.

### Result and safety

- `run(timeout=...)`, `run(env_allow=[...])` (trim inherited env; secrets stay
  out of tools), manifest `env:` applied to the subprocess.
- Errors: `ToolTimeoutError`, `RuntimeProvisionError`,
  `UnsupportedPlatformError`, `ToolExitError` (all under `MainaError`).
