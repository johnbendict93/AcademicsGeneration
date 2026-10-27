import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import run_batch_unit as rbu

print("=" * 70)
print("make_groq_chat implementation:")
print("=" * 70)
print(inspect.getsource(rbu.make_groq_chat))