# parsers_dirs example

Register a custom general parser without forking maina. File stem = parser
name (`shout.py` → `parser: shout`).

```text
examples/parsers_dirs/
  parsers/shout.py
  tools/echo_shout.yaml
  demo.py
```

```python
from pathlib import Path
import maina

here = Path("examples/parsers_dirs")
app = maina.register(
    maina.Runtime.preferred(),
    registry_dirs=[here / "tools"],
    parsers_dirs=[here / "parsers"],
)
app.tool("echo_shout").run(message="hi")
```

Run: `uv run python examples/parsers_dirs/demo.py`
