# Changelog

## 0.1.0

First tagged release.

### Runtime

- `Tool(name)` loads a registry manifest and runs it: install/resolve the
  executable, map keyword arguments to argv, run the process, return a
  structured `ToolResult`.
- Composition `Runtime` with `register(rt, registry_dirs=, parsers=, parsers_dirs=, installers=, language=)`; built-ins use the same path.
- `maina.isolated(rt)` for scoped Runtime swaps.
- CLI: `maina run`, `maina list`, `maina prepare` (with `--check`).

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

### Catalog

- Shipped tools: eslint, fd, git-remote(-add), gitleaks, hadolint, jq, jscpd,
  kubectl-get/-apply, markdownlint, prettier, rg, shellcheck, shfmt, yamlfmt,
  yq.
