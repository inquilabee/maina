# Local tool overlay example

Minimal registry overlay you can register without editing maina:

```text
examples/local_tool/
  hello.yaml
```

```python
from pathlib import Path
import maina

maina.register(maina.runtime(), registry_dirs=[Path("examples/local_tool")])
# Requires `hello` on PATH (install.backend: path):
# maina.Tool("hello").run()
print(maina.Tool("hello"))
```

Later overlay roots override shipped ids with the same name.
