import json
from pathlib import Path

RESOURCES_DIR = Path(r"C:\Users\johnb\Documents\Anna-University-PDF-Academic-Resources")
subjects_file = RESOURCES_DIR / "subjects.json"
cov_file = RESOURCES_DIR / "coverage_reports" / "AL3501_coverage.json"

cov_data = json.loads(cov_file.read_text(encoding="utf-8"))
units_cov = cov_data.get("units", {})

# Extract topic strings for Units I to IV
unit_topics_map = {}
for u_key in ["I", "II", "III", "IV"]:
    topics_list = units_cov.get(u_key, {}).get("topics", [])
    raw_names = []
    for t in topics_list:
        name = t.get("topic") or t.get("name") if isinstance(t, dict) else str(t)
        if name and name.strip():
            raw_names.append(name.strip())
    # Join with standard en-dash delimiter
    unit_topics_map[u_key] = [" – ".join(raw_names)]

print("Extracted topic groups from coverage report:")
for k, v in unit_topics_map.items():
    print(f"  Unit {k}: {len(v[0].split(' – '))} topics")

# Update subjects.json
data = json.loads(subjects_file.read_text(encoding="utf-8"))

def update_node(node):
    count = 0
    if isinstance(node, dict):
        if node.get("code") == "AL3501":
            if "topics" not in node:
                node["topics"] = {}
            for u_key, t_list in unit_topics_map.items():
                node["topics"][u_key] = t_list
            count += 1
        for v in node.values():
            count += update_node(v)
    elif isinstance(node, list):
        for item in node:
            count += update_node(item)
    return count

updated_entries = update_node(data)
subjects_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\nSuccessfully updated {updated_entries} AL3501 entries in {subjects_file.name}!")