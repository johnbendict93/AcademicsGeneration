import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
import source_context as sc
import topic_review as tr
import topic_store as ts
from run_batch_unit import get_neon_conn


def approve_draft_rows(conn):
    print("=" * 65)
    print("Approving all verified draft topic rows in Neon...")
    print("=" * 65)
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE topic_texts
           SET review_status = 'approved',
               reviewed_at = NOW(),
               review_note = 'Batch verified & approved'
         WHERE subject_code = 'CS3591'
           AND unit_number IN (4, 5)
           AND review_status = 'draft'
           AND length(markdown) > 0
        RETURNING id, unit_number, topic
    """
    )
    rows = cur.fetchall()
    conn.commit()
    print(f"Successfully promoted {len(rows)} topic rows to 'approved':")
    for r in rows:
        print(f"  - Unit {r[1]}: {r[2]} (Row ID: {r[0]})")


def compile_unit_pdf(conn, unit_number, unit_title):
    print("\n" + "=" * 65)
    print(f"Compiling Final PDF: CS3591 Unit {unit_number} - {unit_title}")
    print("=" * 65)

    cur = conn.cursor()
    cur.execute(
        """
        SELECT * FROM topic_texts
         WHERE subject_code = 'CS3591'
           AND unit_number = %s
    """,
        (unit_number,),
    )
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(tr.GAP_MARK_ROWS_SQL, ("CS3591", unit_number))
    m_cols = [d[0] for d in cur.description]
    marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]

    ctx = sc.build_unit_context("CS3591", unit_number)
    view = ts.unit_view(ctx, rows, marks)

    print(f"Topic View ({len(view)} total):")
    for e in view:
        print(
            f"  [{e.index:2d}] {e.topic:<34} | State: {e.state:<8} | Gap: {ts.is_gap(e)}"
        )

    meta = {
        "subject_name": "Computer Networks",
        "subject_code": "CS3591",
        "dept_code": "CSE",
        "dept_name": "Computer Science and Engineering",
        "regulation": "2021",
    }

    unit = source_app.build_pdf_from_topics(
        ctx=ctx,
        view=view,
        subject_name=meta["subject_name"],
        subject_code=meta["subject_code"],
        dept_code=meta["dept_code"],
        dept_name=meta["dept_name"],
        unit_number=unit_number,
        unit_title=unit_title,
        regulation=meta["regulation"],
    )

    pdf_filename = f"CS3591_Unit{unit_number}_Notes.pdf"
    output_path = APP_DIR / pdf_filename
    output_path.write_bytes(unit.pdf_bytes)
    print(
        f"\n[DONE] Generated {pdf_filename} ({len(unit.pdf_bytes):,} bytes)"
    )
    print(f"Saved to: {output_path}")


def main():
    conn = get_neon_conn()
    approve_draft_rows(conn)

    units = [
        (1, 'Application Layer'),
        (2, 'Transport Layer'),
        (3, 'Network Layer'),
        (4, 'Routing and Routing Protocols'),
        (5, 'Data Link Layer & Physical Layer'),
    ]

    for unit_num, unit_title in units:
        compile_unit_pdf(conn, unit_num, unit_title)

    print('\n' + '=' * 65)
    print('CS3591 Units 1-5 Notes PDF Compilation Complete!')
    print('=' * 65)

if __name__ == '__main__':
    main()
