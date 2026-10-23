"""
Rerank provider implementations. See app/ai/base.py for the contract.

Closes the TODO.md gap where app/services/retrieval.py:rerank() was a labeled no-op (D011).
"""
from functools import lru_cache

from app.ai.base import RerankError, RerankProvider
from app.core.config import get_settings


@lru_cache
def _get_local_cross_encoder():
    """Loaded lazily and cached, same reasoning as app/ai/embeddings.py's local model loader -
    avoid paying the model-load cost in processes that never rerank anything."""
    from sentence_transformers import CrossEncoder  # local import, see docstring

    settings = get_settings()
    return CrossEncoder(settings.rerank_model_name)


class LocalCrossEncoderRerankProvider(RerankProvider):
    """Local, no paid API key required (D007). NOTE: unlike the embedding/generation local
    providers, this has not been exercised end-to-end in the authoring sandbox - downloading
    the actual cross-encoder model weights requires network access this environment doesn't
    have. The orchestration logic that CALLS this (app/services/retrieval.py:rerank) has been
    verified against a stub scorer - see tests/services/test_reranking.py and the session's
    CHANGELOG.md entry for exactly what was and wasn't verified."""

    def score(self, *, query: str, documents: list[str]) -> list[float]:
        try:
            model = _get_local_cross_encoder()
            pairs = [(query, doc) for doc in documents]
            scores = model.predict(pairs)
            return [float(s) for s in scores]
        except Exception as exc:
            raise RerankError(f"Local cross-encoder rerank failed: {exc}") from exc


class NoOpRerankProvider(RerankProvider):
    """Explicit opt-out (RERANK_ENABLED=false). Returns neutral scores that preserve the
    input order - kept as its own class rather than special-casing "no provider" in
    retrieval.py, so the calling code never needs to know whether reranking is active."""

    def score(self, *, query: str, documents: list[str]) -> list[float]:
        return [0.0 for _ in documents]
