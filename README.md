# pyporter

Call non-Python CLIs from Python. The registry YAML is the source of truth;
`Tool(name)` loads a manifest and runs it.

```python
from pyporter import Tool

jscpd = Tool("jscpd")
result = jscpd(path=["src"], min_lines=5)
print(result.raw, result.parsed)
```

See `adr/0001-registry-first-tool-runtime.md` and
`adr/0002-runtime-register-extendibility.md`.

## Extendibility

Add local tools, parsers, and installer backends without forking pyporter.
Built-ins use the same `register(...)` path. Open a PR only when a tool is worth
sharing in the shipped catalog.

```python
from pathlib import Path
import pyporter

pyporter.register(registry_dirs=[Path("tools")])
pyporter.Tool("mytool").run(...)
```

Later registry dirs override shipped ids for the same tool id. You can also pass
`parsers=`, `parsers_dirs=`, `installers=`, and `language=`. Use
`Tool(..., runtime=rt)` or `pyporter.use(rt)` when isolating a composition
Runtime (tests, multiple catalogs).

## Setup

```bash
uv sync
uv run shipgate install
uv run pyporter prepare jq
uv run python examples/demo.py
```

Tool cache: `~/.cache/pyporter` (override with `PYPORTER_CACHE`).

## Quality gates (ShipGate)

Code quality is enforced with [ShipGate](https://inquilabee.github.io/shipgate/) using the default **full** suite:

```bash
uv run shipgate install
bash scripts/shipgate-link-src.sh   # src-layout import contracts
uv run shipgate format
uv run shipgate check
uv run shipgate refactor check .
uv run shipgate refactor check --strict src
uv run shipgate refactor fix src
uv run shipgate refactor list
uv run shipgate refactor explain default-get
```

`shipgate check` must pass before merge.

## CLI

```bash
pyporter prepare              # ensure runtimes + tools
pyporter prepare jscpd        # one tool (and its required runtime)
pyporter help                 # package help (also the default with no args)
pyporter run shellcheck --help   # that tool's registry help (also bare: run shellcheck)
pyporter run jq filter=. null_input
pyporter run jscpd path=src min_lines=5
pyporter run kubectl/get resource=pods namespace=default
pyporter run git/remote/add name=origin url=https://example.com
```

`run` maps `key=value` tokens to `Tool(tool_id).run(**kwargs)`. Repeat a key for
list values; a bare key sets a flag to true. Tokens after `--` become `extra`.
With no kwargs (or `--help` / `-h`), `run <tool_id>` prints that tool's registry help
instead of attempting a run.
