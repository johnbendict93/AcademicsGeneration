import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn

conn = get_neon_conn()
cur = conn.cursor()

# Get column names and types
cur.execute("""
    SELECT column_name, data_type, is_nullable
      FROM information_schema.columns
     WHERE table_name = 'topic_texts'
     ORDER BY ordinal_position;
""")
print("=" * 60)
print("Columns in topic_texts:")
print("=" * 60)
for r in cur.fetchall():
    print(f"  {r[0]:<20} {r[1]:<15} nullable={r[2]}")

# Get check constraints
cur.execute("""
    SELECT conname, pg_get_constraintdef(c.oid)
      FROM pg_constraint c
      JOIN pg_class t ON c.conrelid = t.oid
     WHERE t.relname = 'topic_texts';
""")
print("\n" + "=" * 60)
print("Constraints on topic_texts:")
print("=" * 60)
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

conn.close()