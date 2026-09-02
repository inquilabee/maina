# ShipGate catalog parity

[ShipGate](https://inquilabee.github.io/shipgate/) is the intended first
consumer of maina as a dependency: it keeps its policy, suites, scopes,
normalizers, reports, and its Python-package installs, and delegates
**binary / npm tool install and argv** to maina.

This page tracks which of ShipGate's non-Python tools maina can already drive.
It is the checklist for the port (ShipGate "Phase 3").

## ShipGate's install model, mapped to maina

ShipGate tool YAML uses `install.manager`:

| ShipGate | maina equivalent |
| --- | --- |
| `manager: python` (16 tools: ruff, ty, bandit, semgrep, deadcode, deptry, pip-audit, radon, vulture, codespell, mdformat, the `gate.*` set) | **stays in ShipGate** — installed into the managed venv with pip |
| `manager: binary` **with** `download.repo` / `asset_template` | maina `backend: github` + explicit `assets:` map |
| `manager: binary` **without** `download` | maina `backend: npm` (`package` is an npm package) |

`asset_template` placeholders (`{version}`, `{os}`, `{arch}`) plus `os_map` /
`arch_map` expand to the per-platform names maina lists under `assets:`. The
bridge in ShipGate will do that expansion when mapping a ShipGate tool to a
maina `Manifest`.

## Tool coverage

| ShipGate tool | Install | maina catalog | Status |
| --- | --- | --- | --- |
| `gitleaks` 8.30.1 | github `gitleaks/gitleaks` | `gitleaks` | added (asset names mirror ShipGate template; verify via integration) |
| `hadolint` 2.14.0 | github `hadolint/hadolint` | `hadolint` 2.12.0 | present; ShipGate can pin its own version via the bridge |
| `shellcheck` 0.11.0 | github `koalaman/shellcheck` | `shellcheck` 0.10.0 | present; version pinned by ShipGate |
| `shfmt` 3.6.0 | github `mvdan/sh` | `shfmt` 3.10.0 | added |
| `yamlfmt` 0.21.0 | github `google/yamlfmt` (Linux-only template) | `yamlfmt` 0.21.0, `platforms: [linux]` | added |
| `jscpd` 5.0.12 | npm `jscpd` | `jscpd` 4.0.5 | present (npm); version pinned by ShipGate |
| `markdownlint` 0.49.1 | npm `markdownlint-cli` | `markdownlint` 0.34.0 | present (npm); version pinned by ShipGate |

Everything ShipGate installs with `manager: binary` now has a maina backend
path. Nothing in ShipGate's Python set moves.

## Open for the port

- The bridge (`shipgate_tool_to_maina_manifest`): `asset_template` expansion,
  `os_map` / `arch_map`, `success_codes`, `subcommand` list, `cli` option
  styles (`scalar` / `positional` / `boolean`) -> maina `args`.
- A session-scoped `maina.Runtime(cache_root=".shipgate/tools")`, threaded
  through ShipGate's parallel runner (never the process session).
- `maina prepare --check` as the "managed tools already satisfied" fast path.
- Decide whether maina's shipped catalog or ShipGate's stays the version source
  of truth for shared tools (current lean: ShipGate pins, maina executes).
