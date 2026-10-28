import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from source_context import _scraper

print("=" * 70)
print("_scraper.clean_unit_topics source code:")
print("=" * 70)
print(inspect.getsource(_scraper.clean_unit_topics))