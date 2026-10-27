import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app

print("=" * 60)
print("source_app.TOPIC_COLUMNS:")
print("=" * 60)
for col in source_app.TOPIC_COLUMNS:
    print(f"  - {col}")