import sys
from pathlib import Path
import json

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

res_dir = Path(sc.RESOURCES_DIR)
subjects_path = res_dir / "subjects.json"
data = json.loads(subjects_path.read_text(encoding="utf-8"))

clean_u5_string = (
    "Discourse segmentation – Coherence – Discourse Reference Phenomena – "
    "Anaphora Resolution using Hobbs and Centering Algorithm – Coreference Resolution – "
    "Resources: Porter Stemmer and Lemmatizer – Penn Treebank and Brill's Tagger – "
    "WordNet, PropBank, FrameNet – Brown Corpus and British National Corpus (BNC)"
)

updated = 0
def walk_and_patch(node):
    global updated
    if isinstance(node, dict):
        code = str(node.get("code") or node.get("subject_code") or "").strip().upper()
        if code == "AL3501":
            if "topics" in node and isinstance(node["topics"], dict):
                node["topics"]["V"] = [clean_u5_string]
                updated += 1
        for v in node.values():
            walk_and_patch(v)
    elif isinstance(node, list):
        for item in node:
            walk_and_patch(item)

walk_and_patch(data)
subjects_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"Updated {updated} AL3501 topic entries in subjects.json")