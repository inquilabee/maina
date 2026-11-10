# Contributing

Thanks for helping. maina is a small project; a focused change with a test is the easiest thing to review.

## Setup

```bash
uv sync                    # runtime + dev dependencies
pre-commit install         # optional: ruff + unit tests before each commit
```

Python 3.11.4 or newer. Linux and macOS are supported (see the README's platform note).

## Checks

These are what CI runs; keep them green:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/unit -q
```

Unit tests are hermetic (no network, no `mise`). The integration suite hits the
network, `mise` and `npm`, and is opt-in:

```bash
uv run pytest -m integration
```

## Docs

The user guide lives in [`docs/`](docs/index.md) and builds with MkDocs:

```bash
uv run mkdocs serve          # http://127.0.0.1:8000
uv run mkdocs build --strict
```

Change the docs in the same pull request as a behaviour change.

## Maintainer-local checks (optional)

The maintainer also runs [ShipGate](https://inquilabee.github.io/shipgate/) locally for
code-quality gates. It is not required for contributions and does not run in CI.

## Reporting security issues

Do not open a public issue; see the [security policy](SECURITY.md).
