import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_store as ts
from run_batch_unit import get_neon_conn

def inspect_rows():
    conn = get_neon_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, topic_index, topic, review_status, source_sha256, passages_sha256, generated_at
        FROM topic_texts
        WHERE subject_code = 'AL3501' AND unit_number = 1 AND topic_index IN (2, 3)
        ORDER BY topic_index, id;
    """)
    rows = cur.fetchall()
    print("=" * 80)
    print("Existing Rows in DB for Topics 2 and 3:")
    print("=" * 80)
    for r in rows:
        print(f"ID: {r[0]:<4} | Index: {r[1]} | Status: {r[3]:<10} | Generated: {r[6]}")
        print(f"   Passages SHA: {r[5]}")

    ctx = sc.build_unit_context("AL3501", 1)
    print("\nExpected Passages SHA from ctx:")
    print(f" - Topic 2: {ts.passages_sha256(ctx.topics[1])}")
    print(f" - Topic 3: {ts.passages_sha256(ctx.topics[2])}")

    conn.close()

if __name__ == "__main__":
    inspect_rows()