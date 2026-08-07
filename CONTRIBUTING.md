# Contributing

## Quality

ShipGate's **full** suite is the merge bar. Keep these green:

```bash
uv run shipgate install
bash scripts/shipgate-link-src.sh
uv run shipgate format
uv run shipgate check
uv run shipgate refactor check .
uv run shipgate refactor check --strict src
```

Local [pre-commit](https://pre-commit.com/) mirrors that bar (format, full check, refactor `--strict`, unit tests), same shape as [ShipGate dogfood](https://github.com/inquilabee/shipgate/blob/main/.pre-commit-config.yaml):

```bash
uv sync
pre-commit install
```

Hooks need local `.shipgate/` and `scripts/shipgate-link-src.sh` (gitignored is fine for personal use).

## Tests

```bash
uv run pytest
```
