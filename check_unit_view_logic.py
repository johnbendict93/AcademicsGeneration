import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_context as sc
import topic_store as ts

print("=" * 70)
print("topic_store.covered & passages_sha256:")
print("=" * 70)
print(inspect.getsource(ts.covered))
print("-" * 50)
print(inspect.getsource(ts.passages_sha256))
print("-" * 50)
print(inspect.getsource(ts.recheck))

ctx = sc.build_unit_context("AL3501", 1)
print("\n" + "=" * 70)
print("Plan status for all 8 topics in ctx:")
print("=" * 70)
for i, plan in enumerate(ctx.topics, 1):
    cov = ts.covered(plan)
    psha = ts.passages_sha256(plan)
    print(f"[{i}] {plan.topic[:40]:<42} | covered: {str(cov):<5} | passages_sha256: {psha}")