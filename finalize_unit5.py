import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
import source_context as sc
import source_prompts as sp
import source_runner as sr
from run_batch_unit import make_groq_chat, save_topic_to_neon

def run():
    print("=" * 65)
    print("Finalizing Unit 5 Missing Topics: Virtual LAN & Circuit Switching.")
    print("=" * 65)

    ctx = sc.build_unit_context("CS3591", 5)
    groq_chat = make_groq_chat()
    rules = sr.prompt_version()

    target_names = ["Virtual LAN", "Circuit Switching."]
    targets = [t for t in ctx.topics if getattr(t, "topic", "") in target_names]

    index_map = {t.topic: i + 1 for i, t in enumerate(ctx.topics)}

    for plan in targets:
        print(f"\nProcessing: {plan.topic}...")
        res = sp.notes_for_topic(
            ctx,
            plan,
            "Computer Networks",
            "Data Link Layer & Physical Layer",
            ask=groq_chat,
            avoid=""
        )
        print(f" -> Status: {res.status} | Markdown len: {len(res.markdown)}")

        row = source_app.topic_row(
            subject_code="CS3591",
            unit_number=5,
            variant="Notes",
            plan=plan,
            index=index_map[plan.topic],
            result=res,
            source_sha256=ctx.source.sha256,
            rules_version=rules,
            avoid_notes=""
        )
        rid = save_topic_to_neon(row)
        print(f" -> Successfully saved to Neon topic_texts (Row ID: {rid})")

    print("\n" + "=" * 65)
    print("All Unit 5 topics processed!")
    print("=" * 65)

if __name__ == "__main__":
    run()