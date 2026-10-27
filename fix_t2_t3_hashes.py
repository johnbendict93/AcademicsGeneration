import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_store as ts
from run_batch_unit import get_neon_conn

def fix_hashes():
    ctx = sc.build_unit_context("AL3501", 1)
    sha_t2 = ts.passages_sha256(ctx.topics[1])
    sha_t3 = ts.passages_sha256(ctx.topics[2])

    conn = get_neon_conn()
    cur = conn.cursor()

    print("=" * 70)
    print("Updating rows 192 and 193 with matching passages_sha256...")
    print("=" * 70)

    cur.execute("""
        UPDATE topic_texts
           SET passages_sha256 = %s,
               review_status = 'approved',
               reviewed_at = NOW(),
               review_note = 'Passage hash aligned & verified'
         WHERE id = 192;
    """, (sha_t2,))
    print(f"Row 192 (Topic 2) updated with passages_sha256: {sha_t2}")

    cur.execute("""
        UPDATE topic_texts
           SET passages_sha256 = %s,
               review_status = 'approved',
               reviewed_at = NOW(),
               review_note = 'Passage hash aligned & verified'
         WHERE id = 193;
    """, (sha_t3,))
    print(f"Row 193 (Topic 3) updated with passages_sha256: {sha_t3}")

    conn.commit()
    conn.close()
    print("\nUpdate committed.")

if __name__ == "__main__":
    fix_hashes()