# Contributing

## Quality

ShipGate runs the **full** suite. Keep these green:

```bash
uv run shipgate install
bash scripts/shipgate-link-src.sh
uv run shipgate format
uv run shipgate check
uv run shipgate refactor check .
uv run shipgate refactor check --strict src
```

## Tests

```bash
uv run pytest
```
