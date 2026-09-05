# Install backends

An install backend turns a manifest's `install:` block into an executable path
plus a process environment. maina ships five (`path`, `github`, `npm`, `pip`,
`script`); you can [register your own](extending.md#custom-install-backends).

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

## `script`

Runs a repo-local file or directory named by `install.entry`. A bare name
resolves under the nearest `scripts/` directory walking up from the manifest
that declared it (the same family convention `parser:` uses for a colocated
`parsers/*.py`); a `/`-containing entry resolves under the manifest's
registry root instead.

```yaml
install:
  backend: script
  entry: deploy.sh
```

No install, no cache — the entry is chmod +x'd and exec'd directly, and its
own shebang (`#!/usr/bin/env python3`, `#!/usr/bin/env bash`, ...) picks the
interpreter. A `requires:` pin is already on `PATH` by the time this runs
(the installer composes `requires:` env before dispatching to any backend),
so the shebang resolves to the pinned interpreter without maina invoking it
explicitly.

`entry` can also name a directory — a small script package. It must contain
exactly one file named like the directory itself (any or no extension, e.g.
`release/release.py`), which is the entry point; sibling files in the
directory are free to import or source each other however their language
allows. See `examples/scripts_dir/`.

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

### Naming assets once

Most projects name every release asset to one pattern. Write it once:

```yaml
install:
  backend: github
  repo: gitleaks/gitleaks
  asset_template: "gitleaks_{version}_{os}_{arch}.tar.gz"
  arch_map: {x86_64: x64}        # maina's token -> the project's spelling
  os_map: {macos: darwin}
```

`{version}` is the manifest's `version:`; `{os}` / `{arch}` are the halves of the
host's platform key after `os_map` / `arch_map` — so `{os}` is maina's own token
(`linux` / `macos` / `windows`), not the project's. Most GitHub releases spell
macOS `darwin`, so `os_map: {macos: darwin}` is the line to reach for first;
maina does not assume it, because plenty of projects ship `macos` or `osx`. An explicit `assets` entry still
wins for its platform, so one oddly-named release does not force every name to be
spelled out. An unknown placeholder is rejected at load, and an expansion that is
not a safe path segment is refused before any download.

`install.allow_any_executable: true` opts in to a fallback: if no archive
member is literally named `binary`, but the archive holds exactly one
executable file, that file is installed as the binary (with a warning).
Off by default — a release asset can otherwise ship a single trojan file
that silently becomes the tool.

## `npm`

Runs `npm install --prefix <cache>` for `package` and resolves the package's
`.bin/<cli>` link. Node is ensured through mise if it is not already on `PATH`
(version from `requires.node`, else `latest`).

```yaml
install:
  backend: npm
  package: markdownlint-cli
```

`--ignore-scripts` by default: a package's install/postinstall lifecycle
scripts run with the composed environment, which is real code execution
from a package the manifest names. `install.allow_scripts: true` opts a
manifest into running them.

## `pip`

Installs a pinned Python package into a **venv of its own** under
`<cache>/tools/<cli>/<version>-<hash>/pip/`, and resolves the console script in
its `bin/`. One venv per tool, never a shared environment — two tools whose
dependencies disagree cannot break each other, and adding a tool cannot alter
what an existing one resolves.

```yaml
install:
  backend: pip
  package: yamllint      # defaults to cli
```

`version:` is required: an unpinned lint tool changes its findings under you.

`cli:` is the **console script** to run and `package:` is the distribution that
provides it — they differ more often than for npm (`cli: ruff`, but a package
whose scripts are named something else needs both spelled out).

**Source distributions are refused by default.** Building an sdist executes the
package's own `setup.py` with the composed environment — the same exposure npm
lifecycle scripts carry — so the install runs `--only-binary=:all:` unless the
manifest opts in with `install.allow_sdist: true`. The package index is never
manifest-settable; a manifest must not be able to redirect where code comes from.

### Pinning the interpreter

With no `requires:` pin the venv is built from the interpreter running maina, so
a catalog that does not care provisions nothing. Pin one, and it is provisioned
through mise like any other runtime:

```yaml
cli: deadcode
version: 2.4.1
install: {backend: pip, package: deadcode}
requires: {python: "3.13"}     # this tool only; the rest follow the host
```

A pin is never satisfied by whatever `python3` happens to be on `PATH` — that
would silently build the venv on the wrong interpreter, which is the one thing
the pin exists to prevent. The pin is also folded into the cache directory hash,
so two tools on different interpreters get different venvs rather than racing
for one.

## Language runtimes (mise)

Runtime provisioning sits behind the `LanguageRuntimeProvider` protocol;
`MiseRuntime` is the shipped implementation. It locates `mise` on `PATH` or
bootstraps a private copy into the cache (needs `curl` and `sh`). Failures raise
`RuntimeProvisionError` (a subclass of `InstallError`).

Only tools that declare `requires:` — or the `npm` backend — touch mise. Static
binaries never do, and `pip` only does when it carries a `python` pin.

## Probing without side effects

`Installer.is_satisfied(manifest)` and `Installer.check_ids()` report install
state without downloading or calling mise. Each shipped backend implements a
`probe(...)` method (`PATH` lookup, cached-file check, npm link check, pip
entry-point check). The
`maina prepare --check` command is the CLI front for this.
