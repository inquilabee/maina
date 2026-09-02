# maina

Call non-Python CLIs from Python with native-like APIs. A YAML **registry** is
the source of truth: `Tool("jq")` loads a manifest, installs or resolves the
executable, maps keyword arguments to argv, runs the process, and returns a
structured result.

```python
from maina import Tool

jq = Tool("jq")
result = jq(filter=".name", path="package.json", raw=True)
print(result.raw)      # stdout as the CLI produced it
print(result.parsed)   # structured value from the manifest's parser, or None
print(result.ok, result.exit_code)
```

## Why

Python code routinely shells out to tools from other ecosystems — Node
(`jscpd`, `prettier`), Go and Rust binaries (`shellcheck`, `hadolint`, `rg`),
system tools (`jq`). Every project re-invents install logic, argv mapping, and
output handling. maina keeps that in one declarative catalog and one runtime, so
no hand-written wrapper per CLI is needed.

## What maina is not

- Not a task runner or a build system.
- Not a policy or quality-gate product (that is
  [ShipGate](https://inquilabee.github.io/shipgate/), which builds on maina).
- Not a shell: commands are argv lists, never a shell string.

## Next

- [Quickstart](quickstart.md) — install, first tool, the CLI.
- [Manifest schema](manifests.md) — every field a registry entry can set.
- [Install backends](backends.md) — `path`, `github`, `npm`, and mise runtimes.
- [Extending maina](extending.md) — add tools, parsers, and backends without a
  fork.
