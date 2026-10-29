import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_review as tr
import topic_store as ts
from run_batch_unit import get_neon_conn

def inspect_u5():
    print("=" * 75)
    print("AL3501 Unit 5 Diagnostics")
    print("=" * 75)

    ctx = sc.build_unit_context("AL3501", 5)
    print(f"Source file: {ctx.source.path.name} ({len(ctx.source.text):,} chars)")
    print(f"Source SHA256: {ctx.source.sha256[:16]}...")
    print(f"Passages generated: {len(ctx.passages)}")
    print(f"Planned topics ({len(ctx.topics)}):")
    for i, tp in enumerate(ctx.topics, 1):
        print(f"  [{i}] {tp.topic} | status={tp.status} | match_ratio={tp.match_ratio:.2f} | passages={len(tp.passages)}")

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

    print(f"\nExisting rows in DB for Unit 5: {len(rows)}")
    for r in rows:
        print(f"  Row {r['id']} | index={r['topic_index']} | topic={r['topic']} | status={r['review_status']}")

    view = ts.unit_view(ctx, rows, marks)
    ready = ts.pdf_ready(view)
    print("\n" + "-" * 75)
    print(f"Reconciled View ({len(view)} topics) | ts.pdf_ready(view) = {ready}")
    for e in view:
        print(f"  [{e.index:2d}] {e.topic:<45} | State: {e.state:<10} | Gap: {ts.is_gap(e)}")

if __name__ == "__main__":
    inspect_u5()