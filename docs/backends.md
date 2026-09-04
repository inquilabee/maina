# Install backends

An install backend turns a manifest's `install:` block into an executable path
plus a process environment. maina ships three; you can
[register your own](extending.md#custom-install-backends).

## Resolution flow

`Tool.run` -> `Installer.resolve(manifest)`:

1. Enforce `platforms:` — an unsupported host raises `UnsupportedPlatformError`.
1. Compose the base environment: `os.environ`, then any `requires:` runtimes
   provisioned through mise and prepended to `PATH`.
1. Take an exclusive lock on the tool's cache directory (safe parallel
   `prepare`).
1. Dispatch to the backend named by `install.backend`.

The cache root is `~/.cache/maina` by default, or `$MAINA_CACHE`, or an explicit
`Runtime(cache_root=...)`. Tools live under `<cache>/tools/<cli>/<version>/`.

## `path` (default)

Resolves `cli` from `PATH`. No download. Fails with `InstallError` when the
binary is not found.

```yaml
install:
  backend: path
```

## `github`

Downloads a release asset over HTTPS, verifies an optional `sha256`, extracts
the named `binary`, and marks it executable. Cached across runs. Streamed with a
512 MiB soft cap; non-HTTPS URLs are refused.

```yaml
install:
  backend: github
  repo: mvdan/sh
  tag: v3.10.0
  binary: shfmt
  assets:
    linux-x86_64: shfmt_v3.10.0_linux_amd64
```

Platform keys are `{os}-{arch}` where `os` is `linux` / `macos` / `windows` and
`arch` is `x86_64` / `arm64`.

`tag` defaults to `version`. `tag: latest` resolves the newest release **once**
and then caches that binary under `<cache>/tools/<cli>/<version>-<hash>/`; it is
not re-checked on later runs. To pick up a newer release, pin a real tag or
delete that cache directory.

## `npm`

Runs `npm install --prefix <cache>` for `package` and resolves the package's
`.bin/<cli>` link. Node is ensured through mise if it is not already on `PATH`
(version from `requires.node`, else `latest`).

```yaml
install:
  backend: npm
  package: markdownlint-cli
```

## Language runtimes (mise)

Runtime provisioning sits behind the `LanguageRuntimeProvider` protocol;
`MiseRuntime` is the shipped implementation. It locates `mise` on `PATH` or
bootstraps a private copy into the cache (needs `curl` and `sh`). Failures raise
`RuntimeProvisionError` (a subclass of `InstallError`).

Only tools that declare `requires:` — or the `npm` backend — touch mise. Static
binaries never do.

## Probing without side effects

`Installer.is_satisfied(manifest)` and `Installer.check_ids()` report install
state without downloading or calling mise. Each shipped backend implements a
`probe(...)` method (`PATH` lookup, cached-file check, npm link check). The
`maina prepare --check` command is the CLI front for this.
