import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

print("=" * 70)
print("sc._load_subjects source:")
print("=" * 70)
print(inspect.getsource(sc._load_subjects))

print("\n" + "=" * 70)
print("sc._subject_group source:")
print("=" * 70)
print(inspect.getsource(sc._subject_group))

res_dir = Path(sc.RESOURCES_DIR)
loaded = sc._load_subjects(res_dir)
print("\nType of loaded subjects:", type(loaded))
if isinstance(loaded, list) and loaded:
    print("Item 0 keys:", loaded[0].keys() if isinstance(loaded[0], dict) else type(loaded[0]))
    print("Item 0 snippet:", str(loaded[0])[:300])
elif isinstance(loaded, dict):
    print("Keys:", list(loaded.keys())[:10])