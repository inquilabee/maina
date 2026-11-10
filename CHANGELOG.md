# Changelog

## Unreleased

## 0.1.1 - 2026-11-10

### Changed

- README: added "What's in the name?" (the package page on PyPI now carries it).

### Internal

- Tag-triggered publish workflow (trusted publishing); the prepare usage-error test no longer
  depends on terminal formatting.

## 0.1.0 - 2026-11-10

First public release (Beta). maina ships **the engine and the manifest contract, not a tool
catalog**: you register your own manifests.

### Added

- **`Tool(name)`** loads a YAML manifest, installs or resolves the executable, maps keyword
  arguments to argv, runs the process, and returns a structured `ToolResult` (`raw`, `parsed`,
  `parse_error`, `exit_code`, `stderr`, `command`, `duration_ms`, and more).
  `Tool.argv()`, `Tool.describe()` and `text=False` (bytes in `raw_bytes`) are available for
  previews, tooling and binary output.
- **Manifests**: flags, positionals, repeats, joiners, defaults, subcommand families with
  `extends` inheritance, `base.yaml` defaults, `abstract` surfaces, `platforms:`, `requires:`
  runtime pins, `env:`, `output.success_codes`, and `output.from_file` for tools that write a
  report. A JSON Schema is published (`maina schema`) and every manifest is validated at load.
- **Install backends**: `path` (an executable already on `PATH`), `github` (release assets with
  optional per-platform `sha256`, `asset_template`), `npm`, `pip` (one venv per tool), and
  `script` (repo-local scripts and script packages). Language runtimes are provisioned through
  [mise](https://mise.jdx.dev/) only when a manifest pins one or the `npm` backend needs Node.
- **Parsers**: built-in general parsers, plus your own via `parsers=` / `parsers_dirs=`.
- **Composition**: `Runtime.preferred(...)`, `maina.register(...)` returning a `MainaApp`,
  `maina.isolated(...)`, and catalogs shipped as wheels through the `maina.registries`
  entry-point group.
- **CLI**: `maina run | list | describe | validate | prepare | clean | schema`, with `--json`,
  `--dry-run`, `--`-passthrough, and stable exit codes (2 for bad usage, 1 for a failed run).
- **Safety controls**: per-call `timeout`, bounded tool and install output, `env_allow` to keep
  secrets out of third-party binaries (including install-phase subprocesses), `sha256` pins.
- User guide (MkDocs), a manifest JSON Schema, and worked catalogs in `examples/` including 30
  real CLIs (`examples/wild_catalog`).

### Security

- A registry root is documented as trusted code; `use_env=False` opts a library out of
  `$MAINA_REGISTRY`.
- Tool ids, manifest paths, `install.entry`, `requires:` pins, archive members and symlinks are
  contained under their roots; tar extraction uses the data filter, and tar and zip extraction have
  size and member-count caps.
- Downloads are HTTPS-only with a redirect-host allowlist; a bare or compressed asset must look
  like an executable before it is cached, so an error page can never become the installed tool.
- The `mise` bootstrap (`curl | sh`) is opt-in via `MAINA_ALLOW_MISE_BOOTSTRAP=1`; `npm` runs
  with `--ignore-scripts` and `pip` refuses source distributions unless the manifest opts in.

### Hardening

- `timeout=` now kills the tool's whole process group, so a wrapper that forks no longer
  outlives the timeout; Ctrl-C and output-cap overflow also stop the tree, and a child left
  holding the output pipes after the tool exits is killed after a short grace period. Tools run
  in their own session, so they have no controlling terminal: a tool that prompts through
  `/dev/tty` fails instead of asking (pass secrets via `stdin=`, `env=`, or flags).
- Relative `registry_dirs` entries are stored absolute, so `script` tools run correctly with an
  explicit `cwd`; a failed `register(replace=True)` no longer empties the registry.
- Corrupt or non-archive downloads, an unwritable cache, malformed `asset_template`, unsafe
  `requires:` pins, null bytes in arguments or environment, and bad `extra` / `stdin` /
  `env_allow` values now raise `MainaError` subclasses instead of raw stdlib exceptions.
- Downloads have a 30-minute wall-clock cap in addition to the per-read timeout.
- `maina prepare` and `prepare --check` report a manifest that cannot load (or that `validate`
  would reject) as a failure with a non-zero exit instead of skipping it or calling it satisfied.
- `maina.isolated(...)` hands the exit-time cleanup hook back to the previous runtime.
- pip packages such as `PyYAML` and `black[d]` are accepted (standard Python package names).

### Platform support

Linux and macOS. Windows is not supported: the install lock is a no-op there and archive
extraction assumes Unix semantics.
