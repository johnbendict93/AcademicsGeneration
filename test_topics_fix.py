import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from source_context import _scraper, _cc

candidate_topics = [
    "Discourse segmentation, Coherence",
    "Discourse Reference Phenomena",
    "Anaphora Resolution using Hobbs and Centering Algorithm",
    "Coreference Resolution",
    "Resources: Porter Stemmer, Lemmatizer",
    "Penn Treebank, Brill's Tagger",
    "WordNet, PropBank, FrameNet",
    "Brown Corpus, British National Corpus (BNC)"
]

print("Testing candidate lines:")
for t in candidate_topics:
    b = bool(_scraper._SYLLABUS_BOILERPLATE_RE.match(t))
    tail = bool(_scraper._SYLLABUS_TAIL_RE.match(t))
    print(f"  '{t}' -> BOILERPLATE: {b}, TAIL: {tail}")

cleaned = _scraper.clean_unit_topics(candidate_topics)
print(f"\n_scraper.clean_unit_topics output ({len(cleaned)} topics):")
for i, c in enumerate(cleaned, 1):
    print(f"  [{i}] {c}")

subtopics = _cc.scoring_subtopics_from_lines(cleaned)
print(f"\n_cc.scoring_subtopics_from_lines output ({len(subtopics)} subtopics):")
for i, s in enumerate(subtopics, 1):
    print(f"  [{i}] {s}")