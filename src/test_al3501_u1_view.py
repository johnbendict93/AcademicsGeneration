import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_store as ts
import topic_review as tr
from run_batch_unit import get_neon_conn

def verify_all_topics():
    conn = get_neon_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT * FROM topic_texts
        WHERE subject_code = 'AL3501' AND unit_number = 1
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(tr.GAP_MARK_ROWS_SQL, ("AL3501", 1))
    m_cols = [d[0] for d in cur.description]
    marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]

    ctx = sc.build_unit_context("AL3501", 1)
    view = ts.unit_view(ctx, rows, marks)

    print("\n" + "=" * 75)
    print(f"AL3501 UNIT 1 RECONCILED VIEW ({len(view)} total topics):")
    print("=" * 75)
    all_ok = True
    for e in view:
        is_ok = (e.state == 'approved' and not ts.is_gap(e))
        if not is_ok:
            all_ok = False
        tag = "[OK - READY]" if is_ok else "[ATTENTION]"
        print(f"{tag:<14} [{e.index:2d}] {e.topic:<50} | State: {e.state:<8} | Gap: {ts.is_gap(e)}")

    print("-" * 75)
    if all_ok:
        print("ALL 8 TOPICS APPROVED AND READY FOR PDF COMPILATION!")
    else:
        print("Some topics still require attention.")

    conn.close()

if __name__ == "__main__":
    verify_all_topics()