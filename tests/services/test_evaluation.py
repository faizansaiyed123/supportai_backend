"""
Tests for app/services/evaluation.py's pure metric functions. Verified locally before being
formalized here - see CHANGELOG.md Session 6 for the smoke-test process (which caught and led
to a fix for the "Sure." short-sentence false-positive in groundedness_score).
"""
from app.services.evaluation import (
    _split_sentences,
    groundedness_score,
    mean_reciprocal_rank,
    recall_at_k,
)


def test_recall_at_k_all_found():
    assert recall_at_k(retrieved_ids=["a", "b", "c"], relevant_ids={"a", "c"}, k=3) == 1.0


def test_recall_at_k_none_found():
    assert recall_at_k(retrieved_ids=["x", "y"], relevant_ids={"a"}, k=2) == 0.0


def test_recall_at_k_partial():
    assert recall_at_k(retrieved_ids=["a", "x"], relevant_ids={"a", "b"}, k=2) == 0.5


def test_recall_at_k_empty_relevant_set_is_perfect_by_convention():
    assert recall_at_k(retrieved_ids=["a"], relevant_ids=set(), k=1) == 1.0


def test_recall_at_k_respects_k_window():
    assert recall_at_k(retrieved_ids=["x", "y", "a"], relevant_ids={"a"}, k=2) == 0.0


def test_reciprocal_rank_first_position():
    assert mean_reciprocal_rank(retrieved_ids=["a", "b"], relevant_ids={"a"}) == 1.0


def test_reciprocal_rank_second_position():
    assert mean_reciprocal_rank(retrieved_ids=["b", "a"], relevant_ids={"a"}) == 0.5


def test_reciprocal_rank_not_found():
    assert mean_reciprocal_rank(retrieved_ids=["x", "y"], relevant_ids={"a"}) == 0.0


def test_reciprocal_rank_empty_retrieved():
    assert mean_reciprocal_rank(retrieved_ids=[], relevant_ids={"a"}) == 0.0


def test_groundedness_well_grounded_answer_scores_high():
    source = ["Refunds are processed within 5 business days of the request being approved."]
    result = groundedness_score(
        answer_text="Refunds are processed within 5 business days.", source_texts=source
    )
    assert result.overall_score >= 0.9
    assert result.unsupported_sentences == []


def test_groundedness_flags_hallucinated_sentence_but_not_grounded_one():
    source = ["Refunds are processed within 5 business days of the request being approved."]
    answer = (
        "Refunds are processed within 5 business days. "
        "We also offer free lifetime warranty on all products purchased before 2020."
    )
    result = groundedness_score(answer_text=answer, source_texts=source)
    assert any("lifetime warranty" in s for s in result.unsupported_sentences)
    assert not any("5 business days" in s for s in result.unsupported_sentences)
    assert 0.0 < result.overall_score < 1.0


def test_groundedness_empty_answer_is_trivially_perfect():
    assert groundedness_score(answer_text="", source_texts=["anything"]).overall_score == 1.0


def test_groundedness_no_sources_means_unsupported():
    result = groundedness_score(answer_text="Some specific factual claim here.", source_texts=[])
    assert result.overall_score < 1.0


def test_groundedness_short_interjection_not_flagged():
    source = ["Refunds are processed within 5 business days."]
    result = groundedness_score(
        answer_text="Sure. Refunds are processed within 5 business days.", source_texts=source
    )
    assert "Sure." not in result.unsupported_sentences


def test_split_sentences_multiple():
    assert len(_split_sentences("One. Two! Three?")) == 3


def test_split_sentences_no_terminal_punctuation():
    assert _split_sentences("just one clause") == ["just one clause"]

