# Worked catalog

One manifest per feature that has real authoring friction:

| File | Shows |
| --- | --- |
| `hello.yaml` | `path` backend — resolve an on-PATH binary, install nothing. The demo runs this one. |
| `jq.yaml` | `github` backend — one release asset per `{os}-{arch}`, `binary:` inside the asset, optional `sha256:` pins, `parser: json`. |
| `shellcheck.yaml` | `github` `.tar.xz` — the binary is located inside the extracted tree; `success_codes: [0, 1]` so "found issues" is still a successful run; a `default:` that forces machine output. |
| `prettier.yaml` | `npm` backend + `requires: { node: "22" }`. Its `parser` arg collides with `run(parser=)` — pass it as `run(args={"parser": "babel"})`. |
| `jscpd.yaml` | `npm` + `output.from_file` — the parser reads `{output}/jscpd-report.json`, not stdout. |
| `git/` | A subcommand family: `git/git.yaml` (abstract root, shared `cli` + `install`) → `git/remote/remote.yaml` → `git/remote/add.yaml` = id `git-remote-add`. |

## Run it

```bash
uv run python examples/catalog/demo.py
```

Only `hello` actually executes; the `github` / `npm` tools are shown through
`Tool.argv()` and `Tool.describe()` so the demo needs no network.

Point your own app at this directory the same way:

```python
rt = maina.Runtime.preferred(use_env=False)
maina.register(rt, registry_dirs=["examples/catalog"])
```
