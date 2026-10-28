import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
import source_context as sc
import topic_review as tr
import topic_store as ts
from run_batch_unit import make_groq_chat, save_topic_to_neon, get_neon_conn

def main():
    print("=" * 70)
    print("AL3501 Unit 5 - Batch Topic Generation & Ingestion")
    print("=" * 70)

    # 1. Verify context before launching
    ctx = sc.build_unit_context("AL3501", 5)
    print(f"Loaded {len(ctx.topics)} topics across {len(ctx.passages)} verified passages.")
    for idx, t in enumerate(ctx.topics, 1):
        print(f"  [{idx}] {t.topic} ({t.status})")

    groq_chat = make_groq_chat()

    def on_progress(e):
        idx = e.get("index", 0)
        tot = e.get("total", 0)
        topic = e.get("topic", "")
        status = e.get("status", "")
        print(f"-> [{idx}/{tot}] {topic} -> {status}")

    print("\nStarting generation via source_app.generate_unit_pdf...")
    out = source_app.generate_unit_pdf(
        variant="Notes",
        subject_code="AL3501",
        subject_name="Natural Language Processing",
        dept_code="AI&DS",
        dept_name="Artificial Intelligence and Data Science",
        unit_number=5,
        unit_title="Discourse Analysis and Lexical Resources",
        regulation="2021",
        groq_chat=groq_chat,
        progress=on_progress,
        save_topic=save_topic_to_neon,
    )

    print("\n" + "=" * 70)
    print("Generation complete! Verifying database rows & unit view...")
    print("=" * 70)

    conn = get_neon_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM topic_texts
         WHERE subject_code = 'AL3501'
           AND unit_number = 5
         ORDER BY topic_index, id;
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(tr.GAP_MARK_ROWS_SQL, ("AL3501", 5))
    m_cols = [d[0] for d in cur.description]
    marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]
    conn.close()

    view = ts.unit_view(ctx, rows, marks)
    ready = ts.pdf_ready(view)

    print(f"Total DB rows saved: {len(rows)}")
    print(f"Reconciled View size: {len(view)} topics")
    print(f"ts.pdf_ready(view) = {ready}")
    for e in view:
        print(f"  [{e.index:2d}] {e.topic:<45} | State: {e.state:<10} | Gap: {ts.is_gap(e)}")

if __name__ == "__main__":
    main()