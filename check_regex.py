import sys
from pathlib import Path
import inspect

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from source_context import _scraper

print("=" * 70)
print("_SYLLABUS_BOILERPLATE_RE pattern:")
print(_scraper._SYLLABUS_BOILERPLATE_RE.pattern)

print("\n" + "=" * 70)
print("_SYLLABUS_TAIL_RE pattern:")
print(_scraper._SYLLABUS_TAIL_RE.pattern)

# Test lines against them
lines = [
    "Discourse segmentation, Coherence",
    "Reference Phenomena",
    "Anaphora Resolution using Hobbs and Centering Algorithm",
    "Coreference Resolution",
    "Resources: Porter Stemmer, Lemmatizer",
    "Penn Treebank, Brill's Tagger",
    "WordNet, PropBank, FrameNet",
    "Brown Corpus, British National Corpus (BNC)"
]

print("\n" + "=" * 70)
print("Testing lines:")
for l in lines:
    b = bool(_scraper._SYLLABUS_BOILERPLATE_RE.match(l))
    t = bool(_scraper._SYLLABUS_TAIL_RE.match(l))
    print(f"'{l}' -> BOILERPLATE: {b} | TAIL: {t}")