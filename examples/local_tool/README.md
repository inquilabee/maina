# Local tool overlay example

Minimal registry overlay you can register without editing pyporter:

```text
examples/local_tool/
  hello.yaml
```

```python
from pathlib import Path
import pyporter

pyporter.register(registry_dirs=[Path("examples/local_tool")])
# Requires `hello` on PATH (install.backend: path):
# pyporter.Tool("hello").run()
print(pyporter.Tool("hello"))
```

Later overlay roots override shipped ids with the same name.
