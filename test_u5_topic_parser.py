import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
from source_context import _scraper, _cc

res_dir = Path(sc.RESOURCES_DIR)
data = sc._load_subjects(res_dir)
canonical, _copies = sc._subject_group(data, "AL3501")
raw = (canonical.get("topics") or {}).get("V", [])

print("=" * 70)
print(f"Raw topics in subjects.json ('V'): {len(raw)}")
for i, line in enumerate(raw, 1):
    print(f"  {i}: {line}")

cleaned = _scraper.clean_unit_topics(raw)
print("\n" + "=" * 70)
print(f"After _scraper.clean_unit_topics: {len(cleaned)}")
for i, line in enumerate(cleaned, 1):
    print(f"  {i}: {line}")

subtopics = _cc.scoring_subtopics_from_lines(cleaned)
print("\n" + "=" * 70)
print(f"After _cc.scoring_subtopics_from_lines: {len(subtopics)}")
for i, line in enumerate(subtopics, 1):
    print(f"  {i}: {line}")