import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
from source_context import _scraper, _cc

res_dir = Path(sc.RESOURCES_DIR)
data = sc._load_subjects(res_dir)
canonical, _ = sc._subject_group(data, "AL3501")
topics_dict = canonical.get("topics", {})

print("=" * 70)
print(f"Keys in canonical['topics']: {list(topics_dict.keys())}")
for k, v in topics_dict.items():
    print(f"Key {repr(k)}: {len(v)} lines -> First 2 lines: {v[:2]}")

print("\n" + "=" * 70)
print("_cc.scoring_subtopics_from_lines source code:")
print("=" * 70)
print(inspect.getsource(_cc.scoring_subtopics_from_lines))