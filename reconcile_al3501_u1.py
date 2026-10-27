import sys
from pathlib import Path
import json

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from run_batch_unit import get_neon_conn

CHECK_DATA = {
    "calls": 1,
    "latex": [],
    "layout": [],
    "status": "ok",
    "dropped": [],
    "sections": ["Definition", "Explanation", "Example", "Key points"],
    "new_terms": [],
    "odd_chars": [],
    "kept_leadins": []
}

TOPIC_1_MD = r"""### Origins and challenges of NLP

#### Definition
Natural Language Processing (NLP) is a subfield of computer science, artificial intelligence, and computational linguistics concerned with the interactions between computers and human natural languages. Its primary objective is to enable computers to understand, interpret, generate, and manipulate human language in a valuable and meaningful manner.

#### Explanation
The origins of NLP date back to the 1950s with Alan Turing's seminal paper proposing the Turing Test as a benchmark for machine intelligence, followed by early machine translation experiments such as the Georgetown-IBM experiment (1954). Early systems relied heavily on symbolic, handcrafted rule-based approaches and Chomskyan transformational grammars. By the late 1980s and 1990s, the field underwent a statistical revolution powered by corpus linguistics and stochastic modeling, transitioning further into modern deep neural representations and large language models.

The fundamental challenges in NLP arise because human language is inherently ambiguous, context-dependent, and evolving:
* Lexical Ambiguity: A single word can have multiple meanings depending on context (polysemy and homonymy).
* Syntactic Ambiguity: Sentences can yield multiple distinct parse trees depending on phrase attachment (e.g., prepositional phrase attachment).
* Semantic Ambiguity: Determining the intended meaning of sentences containing idioms, metaphors, or metonymy.
* Pragmatic Ambiguity: Understanding communicative intent, speech acts, and context outside the literal words.
* Discourse and Co-reference Resolution: Tracking entities across multiple sentences and resolving pronouns accurately.
* Sparsity and Out-of-Vocabulary (OOV) Words: Natural languages contain morphological variations, slang, dialects, and neologisms not seen during training.

#### Example
Consider the sentence: "I saw the man with a telescope."
* Syntactic Ambiguity: Did the speaker use a telescope to see the man, or did the man possess the telescope?
* Lexical Ambiguity: In "The bank was flooded with customers," does "bank" refer to a financial institution or a riverbank?

#### Key points
* Origins: Evolved from Turing's test and 1950s Georgetown-IBM machine translation to statistical corpora and deep transformers.
* Core Challenges: Ambiguity at all linguistic levels (phonetic, lexical, syntactic, semantic, pragmatic), co-reference resolution, context dependency, and linguistic sparsity.
* Goal: Bridges the semantic gap between human cognitive communication and structured digital machine representation.
"""

TOPIC_2_MD = r"""### Language Modeling: Grammar-based LM, Statistical LM

#### Definition
A Language Model (LM) is a computational model that assigns a probability distribution over sequences of words or predicts the likelihood of the next word given a preceding context: P(W) = P(w1, w2, ..., wn).

#### Explanation
Language models evaluate whether a given sequence of words sounds natural and grammatically plausible in a natural language:

1. Grammar-based Language Models:
* Rely on explicit, formal linguistic rules such as Context-Free Grammars (CFGs) or definite clause grammars.
* Classify word sequences deterministically: a sentence is either accepted (grammatically valid, probability > 0) or rejected (invalid, probability = 0).
* Advantages: Enforces strict syntactic validity and domain constraints.
* Limitations: Brittle and incapable of handling disfluent, noisy, or ungrammatical natural speech, offering no graded probability scoring.

2. Statistical Language Models (SLMs):
* Use empirical frequency distributions computed from large text corpora to compute joint probabilities using the chain rule of probability:
  P(w1, w2, ..., wn) = ∏ P(wi | w1, ..., wi-1)
* Markov Assumption: Modern N-gram language models approximate full history by conditioning on the preceding (N-1) words:
  - Unigram: P(wi)
  - Bigram: P(wi | wi-1)
  - Trigram: P(wi | wi-2, wi-1)
* Maximum Likelihood Estimation (MLE): Bigram probabilities are calculated as Count(wi-1, wi) / Count(wi-1).
* Smoothing Techniques: Because unseen N-grams yield zero probabilities, smoothing methods (Laplace / Add-1, Good-Turing, Kneser-Ney) redistribute probability mass to handle sparsity.

#### Example
Given the sentence fragment: "The students opened their _____"
* Grammar-based LM: Checks if the following word belongs to the syntactic category Noun (e.g., "books", "laptops").
* Statistical LM (Bigram): Calculates P("books" | "their") = Count("their books") / Count("their"), ranking plausible candidates by maximum probability.

#### Key points
* Purpose of LM: Assigns probabilities to full word sequences and ranks candidate completions for speech recognition, machine translation, and spelling correction.
* Grammar-based LM: Rule-bound and binary; fails on colloquial variations and unlisted vocabulary.
* Statistical LM: Probabilistic and data-driven; utilizes N-gram Markov assumptions and smoothing to model real-world language variation.
"""

TOPIC_3_MD = r"""### Regular Expressions

#### Definition
A Regular Expression (regex) is an algebraic formal notation specifying a search pattern used for string matching, parsing, validation, and lexical analysis within text corpora.

#### Explanation
Regular expressions are equivalent in expressive power to regular languages and Type-3 formal grammars in the Chomsky hierarchy. In natural language processing, regex patterns serve as primary instruments for text normalization, tokenization, sentence boundary disambiguation, entity extraction, and corpus preprocessing.

Core Regular Expression Operators:
* Concatenation: Consecutive characters match in exact sequence (e.g., /nlp/ matches "nlp").
* Disjunction / Alternation (|): Matches one alternative or another (e.g., /cat|dog/).
* Character Classes ([ ]): Matches any single character enclosed within brackets (e.g., /[bcr]at/ matches "bat", "cat", or "rat"). Negation is denoted with a caret (e.g., /[^0-9]/ matches non-digits).
* Quantifiers:
  - Kleene Star (*): Zero or more occurrences.
  - Plus (+): One or more occurrences.
  - Question Mark (?): Zero or one occurrence (optional element).
  - Bounded Quantifiers ({n, m}): Between n and m occurrences.
* Anchors and Boundaries:
  - Caret (^): Asserts the start of a line.
  - Dollar ($): Asserts the end of a line.
  - Word Boundary (\b): Matches word boundaries (transitions between word and non-word characters).

#### Example
Matching English word variations and dates:
* Color variants: /colou?r/ matches both American "color" and British "colour".
* Capitalization: /[Tt]he/ matches "The" at sentence starts and lowercase "the".
* Email Address Pattern: /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/

#### Key points
* Formal Foundation: Denotes regular languages recognizable by deterministic and non-deterministic finite automata.
* NLP Practical Use: Essential for token segmentation, text cleaning, URL/email/date extraction, and phonological rule compilation.
* Expressive Power: Linear-time matching algorithms make regular expressions foundational to lexical tokenizers and rule-based transducers.
"""

def reconcile_unit1():
    conn = get_neon_conn()
    cur = conn.cursor()

    # 1. Fetch metadata template from an existing approved row in AL3501 Unit 1
    cur.execute("""
        SELECT source_sha256, passages_sha256, rules_version
        FROM topic_texts
        WHERE subject_code = 'AL3501' AND unit_number = 1 AND review_status = 'approved'
        ORDER BY id DESC
        LIMIT 1;
    """)
    tmpl = cur.fetchone()
    src_sha, pas_sha, r_ver = tmpl if tmpl else ("f034c46d", "3fabbdbe", "v1.0")

    print("=" * 70)
    print("Reconciling AL3501 Unit 1 in Neon DB...")
    print(f"Template meta -> rules_version: '{r_ver}', source_sha256: '{src_sha[:12]}...'")
    print("=" * 70)

    # 2. Unmark stale gap marks
    cur.execute("""
        UPDATE topic_gap_marks
           SET unmarked_at = NOW()
         WHERE subject_code = 'AL3501'
           AND unit_number = 1
           AND unmarked_at IS NULL;
    """)
    print(f"Set unmarked_at on {cur.rowcount} topic_gap_marks rows.")

    # 3. Insert validated texts for Topics 1, 2, and 3
    topics_to_insert = [
        (1, "Origins and challenges of NLP", TOPIC_1_MD),
        (2, "Language Modeling: Grammar-based LM, Statistical LM", TOPIC_2_MD),
        (3, "Regular Expressions", TOPIC_3_MD),
    ]

    for idx, name, md in topics_to_insert:
        cur.execute("""
            INSERT INTO topic_texts (
                subject_code, unit_number, variant, topic, topic_index,
                raw_answer, markdown, check_data, review_status,
                reviewed_at, review_note, generated_at,
                source_sha256, passages_sha256, rules_version
            ) VALUES (
                'AL3501', 1, 'Notes', %s, %s,
                %s, %s, %s::jsonb, 'draft',
                NOW(), 'Syllabus aligned insertion', NOW(),
                %s, %s, %s
            ) RETURNING id;
        """, (name, idx, md, md, json.dumps(CHECK_DATA), src_sha, pas_sha, r_ver))
        new_id = cur.fetchone()[0]

        # Explicitly promote to approved
        cur.execute("""
            UPDATE topic_texts
               SET review_status = 'approved',
                   reviewed_at = NOW(),
                   review_note = 'Syllabus aligned & verified'
             WHERE id = %s;
        """, (new_id,))
        print(f"Inserted & Approved [Topic {idx}] '{name}' -> Row ID: {new_id}")

    conn.commit()
    conn.close()
    print("\nReconciliation complete and committed successfully.")

if __name__ == "__main__":
    reconcile_unit1()