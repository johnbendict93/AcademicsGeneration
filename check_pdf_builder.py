import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import topic_store as ts

print("=" * 70)
print("topic_store.pdf_ready source:")
print("=" * 70)
print(inspect.getsource(ts.pdf_ready))