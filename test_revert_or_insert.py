import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn

def test_cleanup():
    conn = get_neon_conn()
    cur = conn.cursor()

    try:
        cur.execute("""
            DELETE FROM topic_texts
            WHERE id IN (191, 192, 193)
            RETURNING id;
        """)
        deleted = cur.fetchall()
        conn.commit()
        print(f"Successfully deleted trial rows: {[d[0] for d in deleted]}")
    except Exception as e:
        conn.rollback()
        print(f"Delete not allowed by guard: {e}")

    conn.close()

if __name__ == "__main__":
    test_cleanup()