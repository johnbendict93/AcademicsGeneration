import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
from run_batch_unit import make_groq_chat, save_topic_to_neon

failed_topics = ["Framing", "Virtual LAN", "Circuit Switching."]

def on_progress(e):
    idx = e.get("index", 0)
    tot = e.get("total", 0)
    topic = e.get("topic", "")
    status = e.get("status", "")
    print(f"[{idx}/{tot}] {topic} -> {status}")

print("=" * 65)
print("Regenerating 3 failed topics for CS3591 Unit 5...")
print("=" * 65)

out = source_app.generate_topics(
    variant="Notes",
    subject_code="CS3591",
    subject_name="Computer Networks",
    unit_number=5,
    unit_title="Data Link Layer & Physical Layer",
    topics=failed_topics,
    groq_chat=make_groq_chat(),
    progress=on_progress,
    save_topic=save_topic_to_neon,
)

print("\n" + "=" * 65)
print("Done. Saved topic row IDs:", out.topic_row_ids)
print("=" * 65)