import sys
from pathlib import Path
import json

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

res_dir = Path(sc.RESOURCES_DIR)
print("=" * 70)
print(f"Inspecting RESOURCES_DIR: {res_dir}")
print("=" * 70)

# Check subjects loader
subjects = sc._load_subjects(res_dir)
print(f"Total subjects loaded: {len(subjects)}")

# Get AL3501 canonical and copies
canonical, copies = sc._subject_group(subjects, "AL3501")
print(f"\nCanonical code: {canonical.get('code')}")
print(f"Canonical title: {canonical.get('title') or canonical.get('subject_name')}")

print("\n--- Canonical Topics for AL3501 by Unit ---")
topics_dict = canonical.get("topics") or {}
for u_key, top_list in topics_dict.items():
    print(f"Unit {u_key}: {len(top_list)} topics")
    if str(u_key) in ("5", "V"):
        for i, t in enumerate(top_list, 1):
            print(f"   [{i}] {t}")

print("\n--- Configured Sources for AL3501 ---")
sources_dict = canonical.get("sources") or {}
for u_key, s_meta in sources_dict.items():
    print(f"Unit {u_key}: file = {s_meta.get('file')}, sha256 = {s_meta.get('content_sha256')[:10]}...")

# Look for candidate files for AL3501 Unit 5 in resources_dir
print("\n--- Looking for AL3501 files in resources_dir ---")
for p in res_dir.rglob("*AL3501*"):
    print(f"  {p.relative_to(res_dir)}")