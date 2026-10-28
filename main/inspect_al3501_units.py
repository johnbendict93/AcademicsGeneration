import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_review as tr
import topic_store as ts
from run_batch_unit import get_neon_conn


def inspect_units():
    conn = get_neon_conn()
    cur = conn.cursor()

    # Check for any pending drafts across Units 2-5
    cur.execute("""
        SELECT unit_number, count(*)
          FROM topic_texts
         WHERE subject_code = 'AL3501'
           AND unit_number IN (2, 3, 4, 5)
           AND review_status = 'draft'
         GROUP BY unit_number
         ORDER BY unit_number;
    """)
    drafts = dict(cur.fetchall())

    print("=" * 80)
    print("AL3501 Units 2-5 Diagnostic Overview")
    print("=" * 80)
    print(f"Pending draft rows by unit: {drafts or 'None'}\n")

    for unit_num in [2, 3, 4, 5]:
        print("=" * 80)
        print(f"--- AL3501 Unit {unit_num} ---")
        print("=" * 80)

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

        try:
            ctx = sc.build_unit_context("AL3501", unit_num)
            view = ts.unit_view(ctx, rows, marks)
            ready = ts.pdf_ready(view)

            print(f"Context source: {getattr(ctx.source, 'name', 'Source')} | Topics: {len(view)} | Ready: {ready}")
            for e in view:
                status_flag = "[READY]" if (e.state == 'approved' or ts.is_gap(e)) else "[BLOCK]"
                print(f"  {status_flag:<8} [{e.index:2d}] {e.topic:<45} | State: {e.state:<10} | Gap: {ts.is_gap(e)}")
        except Exception as exc:
            print(f"  Error building context/view for Unit {unit_num}: {exc}")

    conn.close()


if __name__ == "__main__":
    inspect_units()