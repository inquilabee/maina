# Security model

## A registry root is trusted code

maina hardens the path from a manifest to a running process — the `github`
backend pins digests, enforces HTTPS with a redirect-host allowlist, caps
download and decompression size, and validates archive members; `npm` runs with
`--ignore-scripts` and an anchored `package.json`; the manifest schema rejects
`..`, absolute paths, and shell metacharacters in every value that reaches a
path or an argv token.

None of that makes a **registry directory** safe to point at untrusted content.
A directory you register can:

- ship a `parsers/*.py` module, which maina imports and executes in-process
  with no sandbox (`parser:` in a manifest, or `parsers_dirs=`);
- declare an `install:` block that downloads and runs a binary;
- set an `env:` block on the tool subprocess, including `LD_PRELOAD` /
  `DYLD_INSERT_LIBRARIES`.

So registering a directory is equivalent to trusting the code in it. Treat
`register(rt, registry_dirs=[...])`, `$MAINA_REGISTRY`, and
`Runtime.preferred(discover=True)` the way you would treat adding a directory to
`PYTHONPATH` or installing a package.

## Practical guidance

- **Vendor your catalog.** Keep manifests (and their `parsers/`) in your own
  repository or a package you publish, reviewed like any other code.
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
