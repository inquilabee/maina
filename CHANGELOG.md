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

## 0.1.0

First tagged release.

### Scope

- maina ships **the engine and the manifest contract, not a tool catalog.**
  `Runtime.preferred()` has an empty registry; register your own manifests with
  `register(rt, registry_dirs=[...])` or point `MAINA_REGISTRY` at them.

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
