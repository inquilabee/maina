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

Optional local hook (suite `pre-commit` only, not the merge bar):

```bash
uv sync
pre-commit install
```

The hook needs local `.shipgate/` and `scripts/` (gitignored is fine for personal use).

## Tests

```bash
uv run pytest
```
