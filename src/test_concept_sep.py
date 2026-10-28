import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from source_context import _cc, _scraper

print("=" * 70)
print("_CONCEPT_SEP_RE pattern:")
print(_cc._CONCEPT_SEP_RE.pattern)

# Standard Anna University syllabus format uses ' - ' or ' – ' between syllabus segments:
u5_syllabus = [
    "Discourse segmentation",
    "Coherence",
    "Discourse Reference Phenomena",
    "Anaphora Resolution using Hobbs and Centering Algorithm",
    "Coreference Resolution",
    "Resources: Porter Stemmer and Lemmatizer",
    "Penn Treebank and Brill's Tagger",
    "WordNet, PropBank, FrameNet",
    "Brown Corpus and British National Corpus (BNC)"
]

# When items in the list are joined by ' – ', see how scoring_subtopics_from_lines splits them:
raw_joined = [" – ".join(u5_syllabus)]

cleaned = _scraper.clean_unit_topics(raw_joined)
subtopics = _cc.scoring_subtopics_from_lines(cleaned)

print("\n" + "=" * 70)
print(f"Generated Subtopics ({len(subtopics)} topics):")
print("=" * 70)
for i, s in enumerate(subtopics, 1):
    print(f"  [{i}] {s}")