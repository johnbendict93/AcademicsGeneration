import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

res_dir = Path(sc.RESOURCES_DIR)
sources_al3501 = res_dir / "sources" / "AL3501"

print("=" * 70)
print(f"Checking directory: {sources_al3501}")
print("=" * 70)

if sources_al3501.exists():
    for f in sources_al3501.iterdir():
        print(f"  {f.name} ({f.stat().st_size:,} bytes)")
else:
    print("Directory does not exist. Listing all text/transcript files under resources_dir:")
    for f in res_dir.rglob("*.txt"):
        print(f"  {f.relative_to(res_dir)} ({f.stat().st_size:,} bytes)")

# Also check where subjects metadata is loaded from (JSON/YAML file)
print("\n" + "=" * 70)
print("Looking for subjects metadata file in resources_dir:")
print("=" * 70)
for p in res_dir.glob("*.json"):
    print(f"  {p.name} ({p.stat().st_size:,} bytes)")
for p in (res_dir / "syllabus").glob("*.json") if (res_dir / "syllabus").exists() else []:
    print(f"  syllabus/{p.name}")