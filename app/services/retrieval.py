"""
Hybrid retrieval (D002/D011, RESEARCH.md "Hybrid search"): combines pgvector cosine similarity
with Postgres full-text search, merged via reciprocal rank fusion (RRF), then an optional
rerank step. Every query is scoped to actor.org_id AND to documents with status='ready'
(chunks from a still-processing or failed document should never surface in search results).
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import RerankError
from app.ai.factory import get_rerank_provider
from app.api.deps import Actor
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.knowledge import Chunk, Document

logger = get_logger(__name__)

_RRF_K = 60  # standard RRF damping constant


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    content: str
    heading_path: str | None
    score: float


async def _vector_search(db: AsyncSession, *, actor: Actor, query_embedding: list[float], limit: int):
    # Built as an explicit expression (not a bare "distance" string in order_by) - SQLAlchemy
    # 2.x rejects unadorned string column references in order_by with an ArgumentError, so the
    # same expression object is reused for both the SELECT label and the ORDER BY clause.
    distance_expr = Chunk.embedding.cosine_distance(query_embedding)
    result = await db.execute(
        select(Chunk, distance_expr.label("distance"))
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.org_id == actor.org_id,
            Document.status == "ready",
            Chunk.embedding.is_not(None),
        )
        .order_by(distance_expr)
        .limit(limit)
    )
    return result.all()


async def _fulltext_search(db: AsyncSession, *, actor: Actor, query: str, limit: int):
    # to_tsvector/to_tsquery on the fly here rather than a generated column, to keep the
    # first-cut schema (docs/DATABASE.md) simpler; moving to a generated tsvector column +
    # GIN index is a documented follow-up if full-text search performance needs it
    # (tracked in TODO.md hardening phase).
    #
    # Ordering by the output alias name via text("ts_rank_score DESC") is valid standard SQL
    # (Postgres explicitly allows ORDER BY to reference a SELECT-list alias) and, unlike
    # _vector_search's earlier bug, was always wrapped in text() here rather than passed as a
    # bare Python string - bare strings are what SQLAlchemy 2.x rejects in order_by, not
    # text()-wrapped SQL. Kept explicit and commented so a future edit doesn't quietly drop
    # the text() wrapper and reintroduce that class of bug.
    rank_expr = func.ts_rank_cd(
        func.to_tsvector(text("'english'"), Chunk.content),
        func.plainto_tsquery(text("'english'"), query),
    )
    match_expr = func.to_tsvector(text("'english'"), Chunk.content).op("@@")(
        func.plainto_tsquery(text("'english'"), query)
    )
    result = await db.execute(
        select(Chunk, rank_expr.label("ts_rank_score"))
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.org_id == actor.org_id,
            Document.status == "ready",
            match_expr,
        )
        .order_by(text("ts_rank_score DESC"))
        .limit(limit)
    )
    return result.all()


async def hybrid_search(
    db: AsyncSession,
    *,
    actor: Actor,
    query: str,
    query_embedding: list[float],
    top_n: int | None = None,
) -> list[RetrievedChunk]:
    """`top_n` defaults to settings.rerank_candidate_pool_size (not a small fixed number) -
    the whole point of hybrid_search feeding into rerank() is to hand it a wider candidate
    pool to narrow down via cross-encoder scoring; truncating early here would make reranking
    a no-op in practice. Callers that skip reranking entirely can pass a smaller top_n
    explicitly.
    """
    settings = get_settings()
    if top_n is None:
        top_n = settings.rerank_candidate_pool_size

    vector_rows = await _vector_search(db, actor=actor, query_embedding=query_embedding, limit=top_n)
    fulltext_rows = await _fulltext_search(db, actor=actor, query=query, limit=top_n)

    # Reciprocal rank fusion: each result's score is sum(1 / (k + rank)) across the lists it
    # appears in. This is a standard, parameter-light way to combine two differently-scaled
    # ranking signals (cosine distance vs. ts_rank_cd) without needing to normalize them
    # against each other directly.
    rrf_scores: dict[uuid.UUID, float] = {}
    chunk_by_id: dict[uuid.UUID, Chunk] = {}

    for rank, (chunk, _distance) in enumerate(vector_rows):
        rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
        chunk_by_id[chunk.id] = chunk

    for rank, (chunk, _ts_rank) in enumerate(fulltext_rows):
        rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0.0) + 1.0 / (_RRF_K + rank)
        chunk_by_id[chunk.id] = chunk

    ranked_ids = sorted(rrf_scores.keys(), key=lambda cid: rrf_scores[cid], reverse=True)

    return [
        RetrievedChunk(
            chunk_id=cid,
            document_id=chunk_by_id[cid].document_id,
            content=chunk_by_id[cid].content,
            heading_path=chunk_by_id[cid].heading_path,
            score=rrf_scores[cid],
        )
        for cid in ranked_ids[:top_n]
    ]


def rerank(query: str, candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """Reorders `candidates` by cross-encoder relevance score and truncates to
    settings.rerank_top_n (D011, closes the TODO.md gap where this was a no-op).

    Runs synchronously by design (see RerankProvider.score's docstring - CPU-bound, not I/O-
    bound). Degrades gracefully on failure (Sec. 27): if the rerank provider errors (e.g. the
    cross-encoder model isn't downloaded/available), falls back to the original RRF-ranked
    order rather than failing the whole request - reranking is a quality improvement, not a
    correctness requirement, so its absence should never break an answer.
    """
    if not candidates:
        return candidates

    settings = get_settings()
    provider = get_rerank_provider()

    try:
        scores = provider.score(query=query, documents=[c.content for c in candidates])
    except RerankError as exc:
        logger.warning("rerank_failed_falling_back_to_original_order", error=str(exc))
        return candidates[: settings.rerank_top_n]

    if len(scores) != len(candidates):
        # A provider that returns the wrong number of scores is a contract violation - treat
        # it the same as a failure rather than risk silently mismatching scores to chunks.
        logger.warning(
            "rerank_score_count_mismatch_falling_back_to_original_order",
            expected=len(candidates),
            got=len(scores),
        )
        return candidates[: settings.rerank_top_n]

    scored = list(zip(candidates, scores, strict=False))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    reranked = [
        RetrievedChunk(
            chunk_id=c.chunk_id,
            document_id=c.document_id,
            content=c.content,
            heading_path=c.heading_path,
            score=score,
        )
        for c, score in scored
    ]
    return reranked[: settings.rerank_top_n]
