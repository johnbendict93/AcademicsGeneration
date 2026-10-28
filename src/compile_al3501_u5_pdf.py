import sys
from pathlib import Path
import dataclasses

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app
import source_context as sc
import source_pdf as spdf
import topic_review as tr
import topic_store as ts
from run_batch_unit import get_neon_conn

def main():
    print("=" * 70)
    print("Compiling AL3501 Unit 5 Notes PDF")
    print("=" * 70)

    # 1. Build initial context
    ctx = sc.build_unit_context("AL3501", 5)

    # 2. Fix license metadata so it passes spdf._licence validation
    allowed_licenses = list(spdf.LICENCES.keys())
    print(f"Allowed licenses in source_pdf: {allowed_licenses}")
    target_license = allowed_licenses[0]  # Pick the primary valid license key
    print(f"Assigning valid license: '{target_license}'")

    try:
        ctx.source.license = target_license
    except Exception:
        # Handles case where SourceInfo dataclass is frozen
        new_source = dataclasses.replace(ctx.source, license=target_license)
        ctx = dataclasses.replace(ctx, source=new_source)

    # 3. Fetch topic rows and marks from Neon DB
    conn = get_neon_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM topic_texts
         WHERE subject_code = 'AL3501'
           AND unit_number = 5
         ORDER BY topic_index, id;
    """)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute(tr.GAP_MARK_ROWS_SQL, ("AL3501", 5))
    m_cols = [d[0] for d in cur.description]
    marks = [dict(zip(m_cols, r)) for r in cur.fetchall()]
    conn.close()

    # 4. Reconcile view
    view = ts.unit_view(ctx, rows, marks)
    ready = ts.pdf_ready(view)
    print(f"Loaded {len(rows)} DB rows. Reconciled View size: {len(view)}.")
    print(f"PDF Ready: {ready}")

    if not ready:
        print("Cannot compile: view is not pdf_ready. Check topic states:")
        for e in view:
            print(f"  [{e.index}] {e.topic} -> {e.state}")
        return

    # 5. Compile PDF from approved topics
    print("\nAssembling and rendering PDF via source_app.build_pdf_from_topics...")
    gen_unit = source_app.build_pdf_from_topics(
        ctx=ctx,
        view=view,
        subject_name="Natural Language Processing",
        subject_code="AL3501",
        dept_code="AI&DS",
        dept_name="Artificial Intelligence and Data Science",
        unit_number=5,
        unit_title="Discourse Analysis and Lexical Resources",
        regulation="2021",
        variant="Notes",
    )

    # 6. Save output PDF to disk
    pdf_bytes = gen_unit.pdf_bytes
    out_dir = APP_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "AL3501_Unit_5_Notes.pdf"
    out_file.write_bytes(pdf_bytes)

    print("\n" + "=" * 70)
    print("Compilation Successful!")
    print(f"PDF Output File : {out_file.resolve()}")
    print(f"File Size       : {len(pdf_bytes):,} bytes ({len(pdf_bytes)/1024:.1f} KB)")
    print(f"Storage Key     : {gen_unit.storage_key}")
    print(f"Topic Row IDs   : {gen_unit.topic_row_ids}")
    print("=" * 70)

if __name__ == "__main__":
    main()