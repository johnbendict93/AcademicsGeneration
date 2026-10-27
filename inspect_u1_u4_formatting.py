import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
from source_context import _scraper, _cc

res_dir = Path(sc.RESOURCES_DIR)
data = sc._load_subjects(res_dir)
canonical, _ = sc._subject_group(data, "AL3501")
topics_dict = canonical.get("topics", {})

for u in ["I", "II", "III", "IV"]:
    raw = topics_dict.get(u, [])
    cleaned = _scraper.clean_unit_topics(raw)
    sub = _cc.scoring_subtopics_from_lines(cleaned)
    print("=" * 60)
    print(f"Unit {u}: raw lines = {len(raw)}, subtopics = {len(sub)}")
    print("=" * 60)
    print("Raw:")
    for r in raw:
        print(f"  {r}")
    print("\nResulting Subtopics:")
    for s in sub:
        print(f"  -> {s}")