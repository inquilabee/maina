# Parsers

A parser turns a tool's output into `ToolResult.parsed`. It is any callable
`(raw: str, result: ToolResult) -> Any`. A configured parser is **always
invoked** — even when stdout is empty — so a linter that reports only on stderr
(read `result.stderr`) is still parseable. With no parser configured, `parsed`
is `None`.

A parser that raises never breaks the run: the message lands in
`result.parse_error` and `parsed` stays `None`. Raise `maina.ParseError` for an
"output wasn't what I expected" failure.

## General parsers

Registered by name and referenced from a manifest's `parser:` key:

| Name | Behaviour |
| --- | --- |
| `json` | `json.loads(raw)` — **strict**: raises `ParseError` on malformed JSON; `None` if empty. |
| `json-lenient` | `json.loads(raw)`; falls back to the stripped text; `None` if empty. |
| `lines` | `raw.splitlines()`. |
| `text` | `raw` unchanged. |
| `raw` | alias of `text`. |

```yaml
parser: json
```

## Per-tool parser modules

Set `parser:` to a `*.py` filename. maina resolves it under the nearest
`parsers/` directory walking up from the manifest toward its registry root, and
imports a top-level `parse` function.

```text
registry/git/remote/add.yaml        parser: add.py
registry/git/remote/parsers/add.py  def parse(raw, result): ...
```

## Overriding at call time

`run(parser=...)` wins over the manifest. It accepts a general parser name or a
callable:

```python
Tool("mytool").run(parser="lines")
Tool("mytool").run(parser=lambda raw, result: raw.count("\n"))
```

## Registering your own general parsers

```python
import maina

def parse_csv(raw, result):
    import csv, io
    return list(csv.reader(io.StringIO(raw)))

rt = maina.Runtime.preferred()
maina.register(rt, parsers={"csv": parse_csv})
# or load every parsers_dir/*.py file, registered under its file stem:
maina.register(rt, parsers_dirs=["my_parsers"])
```

Parsers satisfy the `Parser` protocol structurally; wrap one explicitly with
`maina.parser(fn)` if you want a concrete instance.
