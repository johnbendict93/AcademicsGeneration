import sys
from pathlib import Path
import json

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

res_dir = Path(sc.RESOURCES_DIR)
reg_file = res_dir / "sources" / "AL3501" / "sources_registry.json"

print("=" * 70)
print(f"Reading {reg_file}:")
print("=" * 70)
if reg_file.exists():
    data = json.loads(reg_file.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2))
else:
    print("Not found!")