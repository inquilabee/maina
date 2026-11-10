# Security model

## A registry root is trusted code

maina hardens the path from a manifest to a running process — the `github`
backend verifies an optional per-platform `sha256` digest (opt-in: an asset
with no `sha256:` entry installs with TLS integrity only, not a pinned
digest), enforces HTTPS with a redirect-host allowlist, caps download and
decompression size, and validates archive members; `npm` runs with
`--ignore-scripts` and an anchored `package.json` by default (`install.allow_scripts: true` opts a manifest into running install/postinstall
lifecycle scripts); the manifest schema rejects `..`, absolute paths, and
shell metacharacters in every value that reaches a path or an argv token.

None of that makes a **registry directory** safe to point at untrusted content.
A directory you register can:

- ship a `parsers/*.py` module, which maina imports and executes in-process
  with no sandbox (`parser:` in a manifest, or `parsers_dirs=`);
- declare an `install:` block that downloads and runs a binary;
- declare `install: {backend: script, entry: ...}`, which `chmod +x`'s and
  runs a file (or a directory-selected file) straight from the registry — no
  download, no checksum, weaker than even the unsigned `github`/`npm` path;
- set an `env:` block on the tool subprocess, including `LD_PRELOAD` /
  `DYLD_INSERT_LIBRARIES`.

So registering a directory is equivalent to trusting the code in it. Treat
`register(rt, registry_dirs=[...])`, `$MAINA_REGISTRY`, and
`Runtime.preferred(discover=True)` the way you would treat adding a directory to
`PYTHONPATH` or installing a package.

## Practical guidance

- **Vendor your catalog.** Keep manifests (and their `parsers/` / `scripts/`)
  in your own repository or a package you publish, reviewed like any other code.
- **`Runtime.preferred()` reads `$MAINA_REGISTRY` by default.** Pass
  `use_env=False` when building a `Runtime` inside a library or a service that
  should not inherit an operator's environment.
- **`discover=True` loads every installed package** that publishes a
  `maina.registries` entry point. Only enable it in an environment whose
  installed packages you control.
- **`env_allow=[...]`** on `run()` trims the environment handed to the tool and
  to install-phase subprocesses to `ESSENTIAL_ENV` plus the keys you name — use
  it to keep credentials out of third-party binaries. The full essential set is
  in [`maina/core/envfilter.py`](https://github.com/inquilabee/maina/blob/main/src/maina/core/envfilter.py);
  it includes `PATH`, `HOME`, locale, temp dirs, the platform's shared-library
  and CA-bundle variables, and `HTTP(S)_PROXY` / `NO_PROXY`.
- A manifest's own `env:` block is **always** forwarded and can override
  `PATH`; only put non-secret configuration there.
- **`MAINA_ALLOW_MISE_BOOTSTRAP=1`** is the single highest-risk opt-in in the
  installer layer: with it set, a `requires:` runtime pin (or any `npm`
  tool) that mise doesn't already provide fetches `https://mise.run` over
  HTTPS and pipes it into `sh`. The pinned `MISE_VERSION` fixes which mise
  release that script installs, but the script's own content is not
  checksummed or signed. Leave the variable unset unless you have already
  decided to trust `mise.run` — without it, provisioning fails closed with
  `RuntimeProvisionError` instead.
