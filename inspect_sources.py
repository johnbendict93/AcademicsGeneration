import sys
from pathlib import Path
import json

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

subs = sc._load_subjects(sc.RESOURCES_DIR)
print(f"Top-level keys in subs ({len(subs)} total): {list(subs.keys())[:10]}")

canonical, copies = sc._subject_group(subs, "AL3501")
print("\nCanonical subject entry:")
print(json.dumps(canonical, indent=2))

print(f"\nNumber of copies: {len(copies)}")
for idx, copy in enumerate(copies, 1):
    print(f"\n--- Copy {idx} sources ---")
    print(json.dumps(copy.get("sources"), indent=2))