import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import run_batch_unit as rbu

print("=" * 70)
print("run_batch_unit.run_unit implementation:")
print("=" * 70)
print(inspect.getsource(rbu.run_unit))

print("\n" + "=" * 70)
print("run_batch_unit.save_topic_to_neon implementation:")
print("=" * 70)
print(inspect.getsource(rbu.save_topic_to_neon))