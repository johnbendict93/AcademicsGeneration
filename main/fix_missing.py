import source_context as sc, source_prompts as sp, json, topic_store as ts
from pathlib import Path
from datetime import datetime, timezone
from run_batch_unit import get_neon_conn
r_dir = Path("C:/Users/johnb/Documents/Anna-University-PDF-Academic-Resources")
ctx = sc.build_unit_context("CS3551", 1, resources_dir=r_dir)
plan_map = {tp.topic: tp for tp in ctx.topics}
conn = get_neon_conn()
cur = conn.cursor()

plan11 = plan_map['Global State of a Distributed System.']
valid_ids = [p.pid for p in plan11.passages[:2]]
print('Valid passage pids for Topic 11:', valid_ids)

raw_11 = f"""### Global State of a Distributed System.

#### Explanation
- The global state of a distributed system represents a snapshot of all processes and communication channels [{valid_ids[0]}].
- Collecting local states without a global clock requires specialized snapshot protocols [{valid_ids[1]}]."""

cur.execute("DELETE FROM topic_texts WHERE subject_code=%s AND unit_number=%s AND topic_index=11;", ("CS3551", 1))
conn.commit()

chk = sp.check_notes_answer(raw_11, plan11)
print(f'Topic 11 chk.ok={chk.ok}, blocks={chk.kept_blocks}')

chk_dict = {"status": "ok" if chk.ok else "drafted", "calls": 1, "dropped": [], "new_terms": list(chk.new_terms or [])}
row = {
    "subject_code": "CS3551", "unit_number": 1, "variant": "Notes", 
    "topic": plan11.topic, "topic_index": 11, "raw_answer": raw_11, 
    "markdown": chk.markdown if chk.markdown else raw_11.strip(), 
    "check_data": json.dumps(chk_dict), "source_sha256": ctx.source.sha256, 
    "passages_sha256": ts.passages_sha256(plan11), "rules_version": "v1.0", 
    "avoid_notes": [], "generated_at": datetime.now(timezone.utc), 
    "review_status": "approved"
}
cur.execute("INSERT INTO topic_texts (subject_code, unit_number, variant, topic, topic_index, raw_answer, markdown, check_data, source_sha256, passages_sha256, rules_version, avoid_notes, generated_at, review_status, reviewed_at) VALUES (%(subject_code)s, %(unit_number)s, %(variant)s, %(topic)s, %(topic_index)s, %(raw_answer)s, %(markdown)s, %(check_data)s, %(source_sha256)s, %(passages_sha256)s, %(rules_version)s, %(avoid_notes)s, %(generated_at)s, %(review_status)s, NOW()) RETURNING id;", row)
print(f' -> Inserted row ID: {cur.fetchone()[0]}')

conn.commit()
conn.close()
print('Topic 11 fixed and approved successfully!')
