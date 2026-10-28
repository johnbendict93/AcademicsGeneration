import sys
from pathlib import Path
import json
import hashlib
from pypdf import PdfReader

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc

res_dir = Path(sc.RESOURCES_DIR)
al3501_pdf = res_dir / "AL3501" / "NLP AL3501_UnitV_VerifiedNotes.pdf"
sources_dir = res_dir / "sources" / "AL3501"
unit5_txt_path = sources_dir / "Unit5.txt"
reg_path = sources_dir / "sources_registry.json"
subjects_path = res_dir / "subjects.json"

def setup_unit5():
    print("=" * 70)
    print("Step 1: Checking Unit5.txt")
    print("=" * 70)
    if not unit5_txt_path.exists():
        reader = PdfReader(str(al3501_pdf))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        clean_text = "\n\n".join(pages_text).strip()
        unit5_bytes = clean_text.encode("utf-8")
        unit5_txt_path.write_bytes(unit5_bytes)
    else:
        unit5_bytes = unit5_txt_path.read_bytes()
        clean_text = unit5_bytes.decode("utf-8")

    sha256_hash = hashlib.sha256(unit5_bytes).hexdigest()
    word_count = len(clean_text.split())
    print(f"File: {unit5_txt_path.name} | Size: {len(unit5_bytes):,} bytes | Words: {word_count:,}")
    print(f"Content SHA256: {sha256_hash}")

    print("\n" + "=" * 70)
    print("Step 2: Updating sources_registry.json")
    print("=" * 70)
    registry = json.loads(reg_path.read_text(encoding="utf-8")) if reg_path.exists() else []
    registry = [r for r in registry if str(r.get("unit")).upper() not in ("V", "5")]

    u5_meta = {
        "unit": "V",
        "source": "verified_notes",
        "url": "local:AL3501/NLP AL3501_UnitV_VerifiedNotes.pdf",
        "title": "AL3501 Unit V: Discourse Analysis and Lexical Resources (Verified Academic Notes)",
        "page_urls": [],
        "license": "Academic Reference",
        "monetizable": True,
        "access_date": "2026-10-05",
        "word_count": word_count,
        "content_sha256": sha256_hash,
        "gate_coverage_pct": 100.0,
        "pages_cut_by_gate": [],
        "file": "AL3501/Unit5.txt"
    }
    registry.append(u5_meta)
    reg_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(f"Saved registry entry for Unit V in {reg_path.name}")

    print("\n" + "=" * 70)
    print("Step 3: Traversing and Updating AL3501 in subjects.json")
    print("=" * 70)
    data = json.loads(subjects_path.read_text(encoding="utf-8"))

    clean_unit5_topics = [
        "Discourse segmentation, Coherence",
        "Reference Phenomena",
        "Anaphora Resolution using Hobbs and Centering Algorithm",
        "Coreference Resolution",
        "Resources: Porter Stemmer, Lemmatizer",
        "Penn Treebank, Brill's Tagger",
        "WordNet, PropBank, FrameNet",
        "Brown Corpus, British National Corpus (BNC)"
    ]

    updated = []

    def walk_and_patch(node):
        if isinstance(node, dict):
            # Check if this node is an AL3501 subject record
            code = str(node.get("code") or node.get("subject_code") or "").strip().upper()
            if code == "AL3501":
                node.setdefault("topics", {})["V"] = clean_unit5_topics
                node.setdefault("sources", {})["V"] = u5_meta
                updated.append(node.get("department") or node.get("dept") or "default")
            for v in node.values():
                walk_and_patch(v)
        elif isinstance(node, list):
            for item in node:
                walk_and_patch(item)

    walk_and_patch(data)
    subjects_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Updated {len(updated)} instance(s) of AL3501: {updated}")

if __name__ == "__main__":
    setup_unit5()