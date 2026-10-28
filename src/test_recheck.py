import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_store as ts
from run_batch_unit import get_neon_conn

def test_recheck_rows():
    conn = get_neon_conn()
    cur = conn.cursor()

    ctx = sc.build_unit_context("AL3501", 1)

    cur.execute("""
        SELECT id, topic_index, topic, raw_answer, markdown, check_data, review_status, source_sha256, passages_sha256
        FROM topic_texts
        WHERE id IN (17, 9);
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    print("=" * 70)
    print("Testing recheck on original approved rows 17 & 9:")
    print("=" * 70)
    for r in rows:
        plan = ctx.topics[r["topic_index"] - 1]
        ok, why = ts.recheck(r, plan)
        print(f"Row {r['id']} (Topic {r['topic_index']}: {r['topic']}):")
        print(f"  recheck ok: {ok}")
        print(f"  recheck why: {why}")
        print("-" * 50)

    conn.close()

if __name__ == "__main__":
    test_recheck_rows()