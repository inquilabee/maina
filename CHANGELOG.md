# Changelog

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
