import json
from pathlib import Path

cov_path = Path(r"C:\Users\johnb\Documents\Anna-University-PDF-Academic-Resources\coverage_reports\AL3501_coverage.json")
data = json.loads(cov_path.read_text(encoding="utf-8"))

units_data = data.get("units", {})
for u_key in ["I", "II", "III", "IV", "V"]:
    u_info = units_data.get(u_key, {})
    print(f"\n==================== Unit {u_key} ====================")
    print(f"Title: {u_info.get('title')}")
    topics = u_info.get("topics", [])
    print(f"Total Topics: {len(topics)}")
    for i, t in enumerate(topics, 1):
        if isinstance(t, dict):
            print(f"  {i}. {t.get('topic') or t.get('name')}")
        else:
            print(f"  {i}. {t}")