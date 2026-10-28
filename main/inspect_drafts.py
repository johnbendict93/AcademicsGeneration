import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn

def check_drafts():
    conn = get_neon_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT unit_number, topic_index, topic, length(markdown), generated_at
          FROM topic_texts
         WHERE subject_code = 'AL3501'
           AND review_status = 'draft'
         ORDER BY unit_number, topic_index;
    """)
    rows = cur.fetchall()

    print("=" * 80)
    print(f"Pending AL3501 Drafts ({len(rows)} found):")
    print("=" * 80)
    for r in rows:
        print(f"Unit {r[0]} | Topic [{r[1]}] {r[2][:45]:<46} | Length: {r[3]:4d} chars | Generated: {r[4]}")

    conn.close()

if __name__ == "__main__":
    check_drafts()