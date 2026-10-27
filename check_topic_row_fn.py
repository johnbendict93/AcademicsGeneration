import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import source_app

for name in ["topic_row", "build_topic_row", "make_topic_row"]:
    if hasattr(source_app, name):
        fn = getattr(source_app, name)
        print(f"Found source_app.{name}:")
        print(inspect.getsource(fn))
        break
else:
    print("source_app.TOPIC_COLUMNS:")
    for c in source_app.TOPIC_COLUMNS:
        print(f"  - {c}")