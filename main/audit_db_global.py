import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn


def audit_global():
    conn = get_neon_conn()
    cur = conn.cursor()

    print("=" * 105)
    print("GLOBAL NEON DATABASE AUDIT (All Subjects & Units)")
    print("=" * 105)

    # 1. Subject-level summary
    cur.execute("""
        SELECT 
            subject_code,
            COUNT(DISTINCT unit_number) AS total_units,
            COUNT(*) AS total_rows,
            COUNT(*) FILTER (WHERE review_status = 'approved') AS approved_rows,
            COUNT(*) FILTER (WHERE review_status = 'draft') AS draft_rows,
            COUNT(*) FILTER (WHERE review_status = 'rejected') AS rejected_rows,
            COALESCE(SUM(array_length(regexp_split_to_array(markdown, E'\\s+'), 1)), 0) AS total_words
        FROM topic_texts
        GROUP BY subject_code
        ORDER BY subject_code;
    """)
    subject_summaries = cur.fetchall()

    print("\n--- SUBJECT-LEVEL OVERVIEW ---")
    print(f"{'Subject':<12} | {'Units':<6} | {'Total Topics':<14} | {'Approved':<10} | {'Draft':<8} | {'Rejected':<10} | {'Total Words':<12}")
    print("-" * 105)
    for row in subject_summaries:
        subj, units, total, app, draft, rej, words = row
        print(f"{subj:<12} | {units:<6} | {total:<14} | {app:<10} | {draft:<8} | {rej:<10} | {words:<12,}")

    # 2. Detailed unit breakdown per subject
    cur.execute("""
        SELECT 
            subject_code,
            unit_number,
            COUNT(*) AS topics_count,
            COUNT(*) FILTER (WHERE review_status = 'approved') AS approved_count,
            COUNT(*) FILTER (WHERE review_status = 'draft') AS draft_count,
            COUNT(*) FILTER (WHERE review_status = 'rejected') AS rejected_count,
            COALESCE(SUM(array_length(regexp_split_to_array(markdown, E'\\s+'), 1)), 0) AS unit_words
        FROM topic_texts
        GROUP BY subject_code, unit_number
        ORDER BY subject_code, unit_number;
    """)
    unit_rows = cur.fetchall()

    # 3. Gap marks breakdown per subject & unit
    cur.execute("""
        SELECT 
            subject_code,
            unit_number,
            COUNT(*) AS total_gaps,
            COUNT(*) FILTER (WHERE unmarked_at IS NULL) AS active_gaps
        FROM topic_gap_marks
        GROUP BY subject_code, unit_number
        ORDER BY subject_code, unit_number;
    """)
    gap_data = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}

    print("\n\n--- UNIT-BY-UNIT BREAKDOWN ---")
    print(f"{'Subject':<12} | {'Unit':<6} | {'Topics':<8} | {'Approved':<10} | {'Draft':<8} | {'Active Gaps':<12} | {'Words':<10}")
    print("-" * 105)
    for row in unit_rows:
        subj, unit, cnt, app, draft, rej, words = row
        tot_gaps, active_gaps = gap_data.get((subj, unit), (0, 0))
        print(f"{subj:<12} | Unit {unit:<3} | {cnt:<8} | {app:<10} | {draft:<8} | {active_gaps:<12} | {words:<10,}")

    conn.close()
    print("\n" + "=" * 105)


if __name__ == "__main__":
    audit_global()