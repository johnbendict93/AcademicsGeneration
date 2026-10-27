import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn


def run_audit(subject_code="AL3501"):
    conn = get_neon_conn()
    cur = conn.cursor()

    print("=" * 95)
    print(f"DATABASE AUDIT: {subject_code} (Neon PostgreSQL)")
    print("=" * 95)

    # 1. Fetch Drafted/Approved Topics
    cur.execute("""
        SELECT 
            unit_number,
            topic_index,
            topic,
            variant,
            review_status,
            COALESCE(array_length(regexp_split_to_array(markdown, E'\\s+'), 1), 0) AS word_count,
            generated_at,
            reviewed_at
        FROM topic_texts
        WHERE subject_code = %s
        ORDER BY unit_number, topic_index;
    """, (subject_code,))
    topic_rows = cur.fetchall()

    # 2. Fetch Syllabus Gap Marks
    cur.execute("""
        SELECT 
            unit_number,
            topic,
            note,
            marked_at,
            unmarked_at
        FROM topic_gap_marks
        WHERE subject_code = %s
        ORDER BY unit_number, id;
    """, (subject_code,))
    gap_rows = cur.fetchall()
    conn.close()

    # Organize by Unit
    units = sorted(list({r[0] for r in topic_rows} | {r[0] for r in gap_rows}))

    total_words = 0
    total_approved = 0

    for u in units:
        unit_topics = [r for r in topic_rows if r[0] == u]
        unit_gaps = [r for r in gap_rows if r[0] == u]

        print(f"\n--- UNIT {u} ({len(unit_topics)} Topics Covered | {len(unit_gaps)} Gaps Marked) ---")
        print(f"{'Idx':<4} | {'Status':<10} | {'Words':<7} | {'Type':<7} | {'Topic Name'}")
        print("-" * 95)

        for r in unit_topics:
            _, idx, name, variant, status, words, _, _ = r
            total_words += words
            if status == "approved":
                total_approved += 1
            print(f"{idx:<4} | {status:<10} | {words:<7} | {variant:<7} | {name}")

        for g in unit_gaps:
            _, name, note, marked_at, unmarked_at = g
            gap_state = "GAP (ACTIVE)" if unmarked_at is None else "GAP (RESOLVED)"
            print(f"{'-':<4} | {gap_state:<10} | {'-':<7} | {'Gap':<7} | [Gap] {name} ({note})")

    print("\n" + "=" * 95)
    print(f"AUDIT SUMMARY FOR {subject_code}:")
    print(f"  Total Generated Topics : {len(topic_rows)}")
    print(f"  Total Approved Topics  : {total_approved}")
    print(f"  Total Active Gap Marks : {len([g for g in gap_rows if g[4] is None])}")
    print(f"  Total Markdown Words   : {total_words:,} words")
    print("=" * 95)


if __name__ == "__main__":
    run_audit("AL3501")