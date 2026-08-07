# parsers_dirs example

Register a custom general parser without forking pyporter. File stem = parser
name (`shout.py` → `parser: shout`).

```text
examples/parsers_dirs/
  parsers/shout.py
  tools/echo_shout.yaml
  demo.py
```

```python
from pathlib import Path
import pyporter

here = Path("examples/parsers_dirs")
rt = pyporter.Runtime.preferred()
pyporter.register(
    rt,
    registry_dirs=[here / "tools"],
    parsers_dirs=[here / "parsers"],
)
pyporter.Tool("echo_shout", runtime=rt).run(message="hi")
```

Run: `uv run python examples/parsers_dirs/demo.py`
