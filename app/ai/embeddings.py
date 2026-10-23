"""Embedding provider implementations. See app/ai/base.py for the contract."""
import asyncio
from functools import lru_cache

from app.ai.base import EmbeddingError, EmbeddingProvider
from app.core.config import get_settings


@lru_cache
def _get_local_model():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    return SentenceTransformer(settings.embedding_model_name)


def _encode(texts: list[str]) -> list[list[float]]:
    """Synchronous model load + encode. Runs in a worker thread - see embed() for why."""
    model = _get_local_model()
    vectors = model.encode(texts, normalize_embeddings=True)
    return [v.tolist() for v in vectors]


class LocalSentenceTransformersProvider(EmbeddingProvider):
    """Local, CPU-friendly, no paid API key required (D007)."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        settings = get_settings()
        try:
            # Both the first model load and every encode are blocking CPU work. This coroutine
            # is on the request path (rag.py embeds the retrieval query inline), so running them
            # directly on the event loop froze *every* endpoint for the length of a cold load -
            # register/login/list all timed out while the first ask was still downloading
            # BAAI/bge-small-en-v1.5. Offloading keeps the loop responsive, and mirrors how
            # rag.py already offloads the CPU-bound rerank via asyncio.to_thread.
            result = await asyncio.to_thread(_encode, texts)
        except Exception as exc:
            raise EmbeddingError(f"Local embedding failed: {exc}") from exc

        for v in result:
            if len(v) != settings.embedding_dimensions:
                raise EmbeddingError(
                    f"Embedding dimension mismatch: model produced {len(v)}, "
                    f"settings.embedding_dimensions={settings.embedding_dimensions}."
                )
        return result
