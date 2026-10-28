import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn
from inspect_u5_view import inspect_u5

conn = get_neon_conn()
cur = conn.cursor()

# Set review_status to 'approved' and provide reviewed_at = NOW()
cur.execute("""
    UPDATE topic_texts
       SET review_status = 'approved',
           reviewed_at = NOW()
     WHERE subject_code = 'AL3501'
       AND unit_number = 5;
""")
print(f"Successfully approved {cur.rowcount} rows for AL3501 Unit 5.")
conn.commit()
conn.close()

print("\n" + "=" * 70)
print("Reconciled Unit 5 View & Verification:")
print("=" * 70)
inspect_u5()