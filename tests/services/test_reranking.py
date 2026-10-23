"""
Unit tests for app/services/retrieval.py:rerank()'s orchestration logic (sorting, truncation,
graceful degradation), using a monkeypatched rerank provider rather than the real cross-encoder
model.

IMPORTANT, stated plainly: this does NOT verify actual relevance-scoring quality - it verifies
that rerank() correctly sorts/truncates/degrades given WHATEVER scores a provider returns. The
real LocalCrossEncoderRerankProvider (app/ai/reranking.py) has not been executed in this
authoring environment (no network access to download the cross-encoder model weights) - see
CHANGELOG.md for exactly what was and wasn't verified this session.
"""

from app.ai.base import RerankError
from app.services import retrieval as retrieval_module
from app.services.retrieval import RetrievedChunk, rerank


def _make_candidates(n: int) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(chunk_id=f"c{i}", document_id="d1", content=f"content {i}", heading_path=None, score=0.0)
        for i in range(n)
    ]


class _StubProvider:
    def __init__(self, score_fn):
        self._score_fn = score_fn

    def score(self, *, query, documents):
        return self._score_fn(query=query, documents=documents)


def test_rerank_empty_candidates_returns_empty(monkeypatch):
    assert rerank("q", []) == []


def test_rerank_reorders_by_score_descending(monkeypatch):
    candidates = _make_candidates(10)
    # documents[0] gets the highest score, documents[-1] the lowest.
    monkeypatch.setattr(
        retrieval_module,
        "get_rerank_provider",
        lambda: _StubProvider(lambda *, query, documents: list(range(len(documents)))[::-1]),
    )
    result = rerank("q", candidates)
    assert result[0].chunk_id == "c0"
    assert len(result) == retrieval_module.get_settings().rerank_top_n


def test_rerank_falls_back_to_original_order_on_provider_error(monkeypatch):
    candidates = _make_candidates(10)

    def _raise(*, query, documents):
        raise RerankError("model unavailable")

    monkeypatch.setattr(retrieval_module, "get_rerank_provider", lambda: _StubProvider(_raise))
    result = rerank("q", candidates)
    top_n = retrieval_module.get_settings().rerank_top_n
    assert [c.chunk_id for c in result] == [f"c{i}" for i in range(top_n)]


def test_rerank_falls_back_on_score_count_mismatch(monkeypatch):
    candidates = _make_candidates(10)
    monkeypatch.setattr(
        retrieval_module,
        "get_rerank_provider",
        lambda: _StubProvider(lambda *, query, documents: [1.0, 2.0]),  # wrong length
    )
    result = rerank("q", candidates)
    top_n = retrieval_module.get_settings().rerank_top_n
    assert [c.chunk_id for c in result] == [f"c{i}" for i in range(top_n)]


def test_rerank_equal_scores_preserve_original_order(monkeypatch):
    candidates = _make_candidates(10)
    monkeypatch.setattr(
        retrieval_module,
        "get_rerank_provider",
        lambda: _StubProvider(lambda *, query, documents: [0.0] * len(documents)),
    )
    result = rerank("q", candidates)
    top_n = retrieval_module.get_settings().rerank_top_n
    assert [c.chunk_id for c in result] == [f"c{i}" for i in range(top_n)]

