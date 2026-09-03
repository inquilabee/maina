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
- tar extraction requires the data filter (Python 3.11.4+), validates
  symlink/hardlink targets, and wraps `TarError` as `InstallError`; the
  single-file archive fallback only accepts an executable file. Binary copies
  into the cache are atomic (`os.replace`).
- `requires-python` is now `>=3.11.4`.

### CLI

- `maina --version` / `maina.__version__`.
- Bad manifests / unknown ids print one line and exit 2 (no Rich traceback).
- Non-success tool run exits 1 (2 stays "bad usage").
- `maina run <id> --` runs a no-required-args tool; `key:=value` forces a
  literal string value.

### API

- `ArgError` and `AbstractToolError` are exported from `maina`.
- `ToolTimeoutError` subclasses `MainaError` directly (not `InstallError`).
- `ParseError` / `ToolExecError` added and exported.
- `Tool.manifest` / `Tool.args` (read-only) / `Tool.describe()` (JSON-safe,
  stable) for building your own help / form / CLI.
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
- A relative `MAINA_CACHE` / `cache_root` is resolved to an absolute path (a
  relative `argv[0]` broke a run with an explicit `cwd`).
- A failed `subprocess` start (bad `cwd`, missing exe) raises `ToolExecError`,
  not a bare `OSError`.
- `Tool.run` defaults stdin to `DEVNULL`; a stdin-reading tool no longer blocks
  on the caller's terminal. Pass `stdin=` to feed it.

### Manifest expressiveness

- `args.<name>.type: pairs` emits `--flag key value` per pair (`jq --arg`,
  `docker -e K=V`).
- `args.<name>.separator` emits a literal token before an arg (e.g. `--` before
  positional paths).
- `output.from_file: "<template>"` — the parse input becomes that file's
  contents (for tools that write a report file); `result.meta["parsed_from"]`
  records it.
- Load-time validation extended: `join` must be `csv`, `timeout` must be a
  number, `platforms` values must be `linux` / `macos` / `windows`.
- CLI: `--json` after `--` reaches the tool (was stripped from the whole line).

### Results and parsers

- `parser: json` is now **strict** — malformed JSON raises `ParseError`
  (recorded in `ToolResult.parse_error`, `parsed=None`, the run does not
  crash). New `json-lenient` keeps the old swallow-and-return-text behaviour.
- A configured parser is invoked even when stdout is empty (stderr-only tools
  are parseable).
- `ToolResult` gains `parse_error`, `cwd`, `exe`, and `to_dict()` (JSON-safe,
  stable key set).

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
