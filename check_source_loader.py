import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

print("=" * 70)
print("sc.load_unit_source source:")
print("=" * 70)
print(inspect.getsource(sc.load_unit_source))

print("\n" + "=" * 70)
print("sc.unit_topics source:")
print("=" * 70)
print(inspect.getsource(sc.unit_topics))

# Check where sources/resources directory is defined
for attr in ["SOURCES_DIR", "RESOURCES_DIR", "DATA_DIR", "SYLLABUS_DIR"]:
    if hasattr(sc, attr):
        print(f"{attr} = {getattr(sc, attr)}")