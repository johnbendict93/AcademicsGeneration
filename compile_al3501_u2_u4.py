import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
import source_context as sc
import topic_review as tr
import topic_store as ts
from run_batch_unit import get_neon_conn

UNITS = [
    (2, "Language Modeling and Part of Speech Tagging"),
    (3, "Context Free Grammar and Parsing"),
    (4, "Semantics and Word Sense Disambiguation"),
]

def compile_units():
    conn = get_neon_conn()

    for unit_num, unit_title in UNITS:
        print("\n" + "=" * 75)
        print(f"Compiling AL3501 Unit {unit_num}: {unit_title}")
        print("=" * 75)

        cur = conn.cursor()
        cur.execute("""
            SELECT * FROM topic_texts
             WHERE subject_code = 'AL3501'
               AND unit_number = %s
        """, (unit_num,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        cur.execute(tr.GAP_MARK_ROWS_SQL, ("AL3501", unit_num))
        m_cols = [d[0] for d in cur.description]
        marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]

        ctx = sc.build_unit_context("AL3501", unit_num)
        view = ts.unit_view(ctx, rows, marks)

        if not ts.pdf_ready(view):
            print(f"Skipping Unit {unit_num}: Not ready for PDF compilation.")
            continue

        unit = source_app.build_pdf_from_topics(
            ctx=ctx,
            view=view,
            subject_name="Natural Language Processing",
            subject_code="AL3501",
            dept_code="AIDS",
            dept_name="Artificial Intelligence and Data Science",
            unit_number=unit_num,
            unit_title=unit_title,
            regulation="2021",
        )

        pdf_filename = f"AL3501_Unit{unit_num}_Notes.pdf"
        output_path = APP_DIR / pdf_filename
        output_path.write_bytes(unit.pdf_bytes)

        covered = sum(1 for e in view if not ts.is_gap(e))
        gaps = sum(1 for e in view if ts.is_gap(e))
        print(f"[DONE] Generated {pdf_filename} ({len(unit.pdf_bytes):,} bytes)")
        print(f"Topics: {len(view)} total ({covered} covered & approved, {gaps} documented gaps)")
        print(f"Saved: {output_path}")

    conn.close()

if __name__ == "__main__":
    compile_units()