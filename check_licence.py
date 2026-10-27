import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_pdf as spdf
import source_context as sc

print("=" * 70)
print("source_pdf._licence implementation:")
print("=" * 70)
print(inspect.getsource(spdf._licence))

ctx = sc.build_unit_context("AL3501", 5)
print("\n" + "=" * 70)
print(f"Current ctx.source.license: {repr(ctx.source.license)}")
print("=" * 70)