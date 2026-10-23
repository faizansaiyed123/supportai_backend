"""
RAG evaluation metrics (Sec. 25, closes a gap flagged in TODO.md). Plain functions per D004.

Two kinds of function here:
1. Retrieval-quality metrics (recall_at_k, mean_reciprocal_rank) - standard information-
   retrieval metrics, pure math, no dependencies. VERIFIED locally (see
   tests/services/test_evaluation.py and this session's CHANGELOG.md entry).
2. Groundedness/hallucination heuristics (groundedness_score, hallucination_flags) - a
   lexical-overlap heuristic, NOT an LLM-as-judge. This is a deliberate, documented choice:
   an LLM-as-judge approach would give richer signal but requires a working generation
   provider to evaluate itself with, which is circular for catching that same provider's
   hallucinations, and adds a second AI call's worth of latency/cost to every eval run. The
   heuristic here is a first-line, imperfect signal (flags sentences in the answer with no
   substantial lexical overlap with any retrieved source) - explicitly not a substitute for
   human review of flagged cases. An LLM-as-judge mode is a documented future enhancement
   (see docs/RAG.md), not built.
"""
import re
from dataclasses import dataclass, field


def recall_at_k(*, retrieved_ids: list, relevant_ids: set, k: int) -> float:
    """Fraction of relevant_ids that appear in the top-k retrieved_ids. 1.0 if relevant_ids is
    empty (nothing to miss)."""
    if not relevant_ids:
        return 1.0
    top_k = set(retrieved_ids[:k])
    found = top_k & relevant_ids
    return len(found) / len(relevant_ids)


def mean_reciprocal_rank(*, retrieved_ids: list, relevant_ids: set) -> float:
    """Reciprocal rank of the first relevant item found in retrieved_ids (1-indexed), 0.0 if
    none of relevant_ids appear at all. Despite the name this function computes ONE query's
    reciprocal rank; "mean" refers to how it's typically averaged across many queries by the
    caller (see EvalRunSummary.mean_reciprocal_rank below)."""
    for rank, item_id in enumerate(retrieved_ids, start=1):
        if item_id in relevant_ids:
            return 1.0 / rank
    return 0.0


_WORD_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "at", "for", "with", "and", "or", "but", "it", "this", "that", "these", "those",
    "as", "by", "from", "you", "your", "i", "we", "they", "he", "she", "will", "can", "could",
    "should", "would", "may", "might", "not", "no", "do", "does", "did",
}
_MIN_SIGNIFICANT_WORDS_TO_SCORE = 3  # short interjections ("Sure.", "Got it.", "Okay, thanks.")
# aren't substantive factual claims worth grounding-checking - flagging them as "unsupported"
# would be noise, not signal. Found via local verification (see CHANGELOG.md) - the initial
# version only skipped fully-empty sentences, which still flagged e.g. "Sure." (one
# significant word) as an ungrounded claim.


def _significant_words(text: str) -> set[str]:
    words = _WORD_RE.findall(text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _split_sentences(text: str) -> list[str]:
    # Deliberately simple - a full sentence tokenizer (e.g. nltk/spacy) is more accurate but
    # is a real added dependency for a heuristic that's already approximate; splitting on
    # terminal punctuation is good enough to flag which PARTS of an answer lack support,
    # which is the heuristic's actual job (see module docstring).
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip()]


@dataclass
class GroundednessResult:
    overall_score: float  # fraction of sentences with adequate source overlap, 0.0-1.0
    unsupported_sentences: list[str] = field(default_factory=list)


def groundedness_score(
    *, answer_text: str, source_texts: list[str], overlap_threshold: float = 0.3
) -> GroundednessResult:
    """For each sentence in answer_text, checks whether a meaningful fraction of its
    significant (non-stopword) words also appear in at least one source text. Sentences below
    `overlap_threshold` are flagged as unsupported - a proxy for "might be hallucinated or not
    actually grounded in the retrieved material," not a certainty (see module docstring)."""
    sentences = _split_sentences(answer_text)
    if not sentences:
        return GroundednessResult(overall_score=1.0, unsupported_sentences=[])

    source_word_sets = [_significant_words(t) for t in source_texts]
    unsupported = []

    for sentence in sentences:
        sentence_words = _significant_words(sentence)
        if len(sentence_words) < _MIN_SIGNIFICANT_WORDS_TO_SCORE:
            continue  # too short to be a substantive factual claim - see module-level note.

        best_overlap = 0.0
        for source_words in source_word_sets:
            if not source_words:
                continue
            overlap = len(sentence_words & source_words) / len(sentence_words)
            best_overlap = max(best_overlap, overlap)

        if best_overlap < overlap_threshold:
            unsupported.append(sentence)

    scored_sentences = [s for s in sentences if len(_significant_words(s)) >= _MIN_SIGNIFICANT_WORDS_TO_SCORE]
    if not scored_sentences:
        return GroundednessResult(overall_score=1.0, unsupported_sentences=[])

    supported_count = len(scored_sentences) - len(unsupported)
    return GroundednessResult(
        overall_score=supported_count / len(scored_sentences),
        unsupported_sentences=unsupported,
    )


@dataclass
class EvalCaseResult:
    case_id: str
    recall_at_5: float
    reciprocal_rank: float
    groundedness: GroundednessResult


@dataclass
class EvalRunSummary:
    case_results: list[EvalCaseResult]

    @property
    def mean_recall_at_5(self) -> float:
        if not self.case_results:
            return 0.0
        return sum(r.recall_at_5 for r in self.case_results) / len(self.case_results)

    @property
    def mean_reciprocal_rank(self) -> float:
        if not self.case_results:
            return 0.0
        return sum(r.reciprocal_rank for r in self.case_results) / len(self.case_results)

    @property
    def mean_groundedness(self) -> float:
        if not self.case_results:
            return 0.0
        return sum(r.groundedness.overall_score for r in self.case_results) / len(self.case_results)
