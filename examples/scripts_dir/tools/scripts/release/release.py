#!/usr/bin/env python3
import importlib.util
from pathlib import Path

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("helper", here / "helper.py")
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {here / 'helper.py'}")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

print(helper.GREETING)
