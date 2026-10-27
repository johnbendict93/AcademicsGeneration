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


def compile_al3501_unit1():
    print("=" * 70)
    print("Compiling Final PDF: AL3501 Unit 1 - Introduction to NLP")
    print("=" * 70)

    conn = get_neon_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM topic_texts
         WHERE subject_code = 'AL3501'
           AND unit_number = 1
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(tr.GAP_MARK_ROWS_SQL, ("AL3501", 1))
    m_cols = [d[0] for d in cur.description]
    marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]
    conn.close()

    ctx = sc.build_unit_context("AL3501", 1)
    view = ts.unit_view(ctx, rows, marks)

    print(f"Verified {len(view)} topics in view:")
    for e in view:
        print(f"  [{e.index:2d}] {e.topic:<48} | State: {e.state:<8} | Gap: {ts.is_gap(e)}")

    ready = ts.pdf_ready(view)
    print("-" * 70)
    print(f"ts.pdf_ready(view) = {ready}")
    if not ready:
        raise RuntimeError("Unit view is not ready for PDF compilation!")

    meta = {
        "subject_name": "Natural Language Processing",
        "subject_code": "AL3501",
        "dept_code": "AIDS",
        "dept_name": "Artificial Intelligence and Data Science",
        "regulation": "2021",
        "unit_number": 1,
        "unit_title": "Introduction to NLP & Morphology",
    }

    unit = source_app.build_pdf_from_topics(
        ctx=ctx,
        view=view,
        subject_name=meta["subject_name"],
        subject_code=meta["subject_code"],
        dept_code=meta["dept_code"],
        dept_name=meta["dept_name"],
        unit_number=meta["unit_number"],
        unit_title=meta["unit_title"],
        regulation=meta["regulation"],
    )

    pdf_filename = f"AL3501_Unit{meta['unit_number']}_Notes.pdf"
    output_path = APP_DIR / pdf_filename
    output_path.write_bytes(unit.pdf_bytes)

    print("\n" + "=" * 70)
    print(f"[DONE] Successfully generated {pdf_filename} ({len(unit.pdf_bytes):,} bytes)")
    print(f"Saved to: {output_path}")
    print("=" * 70)


if __name__ == "__main__":
    compile_al3501_unit1()