<p align="center">
  <img src="https://raw.githubusercontent.com/inquilabee/maina/main/docs/images/maina-mark.svg" alt="maina" width="160">
</p>

# maina

[![CI](https://github.com/inquilabee/maina/actions/workflows/ci.yml/badge.svg)](https://github.com/inquilabee/maina/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/inquilabee/maina/blob/main/LICENSE)
[![Python 3.11.4+](https://img.shields.io/badge/python-3.11.4%2B-blue.svg)](https://github.com/inquilabee/maina/blob/main/pyproject.toml)

Call non-Python CLIs from Python. Describe a tool once in a YAML manifest;
`Tool(name)` installs or finds the executable, maps keyword arguments to argv,
runs the process, and returns a structured result — no hand-written install,
argv, or environment plumbing.

maina ships **the engine and the manifest contract, not a tool catalog**: you
register your own manifests (or your app does).

## Install

```bash
pip install maina      # or: uv add maina
```

Python 3.11.4 or newer, on Linux or macOS.

## A first tool

`my_tools/greet.yaml` — wrap the `echo` already on your `PATH`:

```yaml
cli: echo
install: {backend: path}
args:
  message: {positional: true, required: true}
```

```python
from pathlib import Path

import maina

app = maina.register(maina.Runtime.preferred(), registry_dirs=[Path("my_tools")])
result = app.tool("greet")(message="hello from maina")
print(result.ok, result.exit_code, repr(result.raw))
# True 0 'hello from maina\n'
```

Swap `backend: path` for `github`, `npm`, `pip`, or `script` and maina downloads
and caches the tool for you (digest-pinned if you give it a `sha256`). The
[quickstart](https://inquilabee.github.io/maina/user/quickstart/) walks through a
real `shellcheck` manifest with a JSON parser.

## What you get

- **Five install backends** — an executable already on `PATH`, GitHub release
  assets, npm packages, pip packages (one venv per tool), and repo-local scripts —
  with `mise`-provisioned language runtimes when a manifest pins one.
- **Manifest to argv** — flags, positionals, repeats, defaults, subcommand
  families, and `extends` inheritance. Commands are argv lists, never a shell string.
- **Structured results** — raw stdout, a parsed value (`json` and friends, or your
  own parser), exit code, stderr, timing. `raise_for_status()` when you want exceptions.
- **Safe-by-default runs** — per-call `timeout`, bounded output, `env_allow` to keep
  secrets out of third-party binaries, verified archive extraction.
- **A CLI** — `maina list | describe | validate | prepare | run`.
- **Typed** — ships `py.typed`; errors derive from `MainaError`.

## What's in the name?

The myna (also spelled *maina*) is a bird, and the logo is a Common myna. Birds spend
their lives carrying things from one place to another, and that is just what this
project does: it carries a call from Python over to a program written in another
language, and brings the answer back as data. The bird is where the name comes from.

<p align="center">
  <img src="https://raw.githubusercontent.com/inquilabee/maina/main/docs/images/maina-myna.jpg" alt="A Common myna perched on a stone post" width="560">
  <br>
  <sub>A Common myna. Photo by Hobbyfotowiki, <a href="https://commons.wikimedia.org/wiki/File:Common_myna,_(Acridotheres_tristis).jpg">Wikimedia Commons</a> (CC0).</sub>
</p>

## Platform support

Linux and macOS are supported. Windows is not: the install lock is a no-op there and
archive extraction assumes Unix semantics.

## Documentation

The [user guide](https://inquilabee.github.io/maina/) covers the
[quickstart](https://inquilabee.github.io/maina/user/quickstart/),
[manifest schema](https://inquilabee.github.io/maina/user/manifests/),
[install backends](https://inquilabee.github.io/maina/user/backends/),
[parsers](https://inquilabee.github.io/maina/user/parsers/),
[extending maina](https://inquilabee.github.io/maina/user/extending/) and the
[CLI](https://inquilabee.github.io/maina/user/cli/).
Worked catalogs live in [`examples/`](https://github.com/inquilabee/maina/tree/main/examples),
including 30 real CLIs in [`examples/wild_catalog`](https://github.com/inquilabee/maina/tree/main/examples/wild_catalog).

## Security

A registered registry directory is trusted code — it can ship a `parsers/*.py` that
maina imports and runs in-process, and `Runtime.preferred()` honours `$MAINA_REGISTRY`
unless you pass `use_env=False`. Read the
[security model](https://inquilabee.github.io/maina/user/security/) before pointing
maina at a catalog you did not write, and report vulnerabilities as described in the
[security policy](https://github.com/inquilabee/maina/blob/main/SECURITY.md).

## Contributing

See [contributing guide](https://github.com/inquilabee/maina/blob/main/CONTRIBUTING.md).
MIT licensed.
