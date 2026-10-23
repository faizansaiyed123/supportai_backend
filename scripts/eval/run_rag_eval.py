"""
RAG evaluation harness (Sec. 25). Runs eval/sample_dataset.json's cases against the REAL,
live pipeline (hybrid_search + rerank + generation) for a given organization, and reports
retrieval and groundedness metrics using app/services/evaluation.py's pure metric functions.

*** NOT EXECUTED IN THIS ENVIRONMENT ***
This script needs a live Postgres+pgvector database with actual ingested documents, and a
working AI provider (Ollama/embeddings) - none of which exist in the authoring sandbox (no
network access, no Docker, no Postgres). The metric FUNCTIONS it calls
(app/services/evaluation.py) have been verified locally in isolation; this script's own
control flow (argument handling, report formatting, DB session usage) has been reviewed
carefully but NOT run. Treat this as a well-reasoned starting point that needs a real run
(and likely minor fixes) once you have the stack up, not as verified-working code.

Usage (once the stack is running):
    docker compose exec api python -m scripts.eval.run_rag_eval \\
        --org-slug acme --dataset eval/sample_dataset.json

Prerequisite: the documents named in the dataset's `relevant_document_titles` must already be
uploaded and ingested (status='ready') for the target org - this script evaluates retrieval
against whatever knowledge base content actually exists, it does not seed one.
"""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.ai.factory import get_embedding_provider
from app.api.deps import Actor
from app.db.base import AsyncSessionLocal
from app.models.knowledge import Chunk, Document
from app.models.organization import Organization
from app.services.conversation import create_conversation
from app.services.evaluation import (
    EvalCaseResult,
    EvalRunSummary,
    groundedness_score,
    mean_reciprocal_rank,
    recall_at_k,
)
from app.services.rag import answer_question
from app.services.retrieval import hybrid_search


async def _resolve_org_id(db, org_slug: str) -> uuid.UUID:
    result = await db.execute(select(Organization).where(Organization.slug == org_slug))
    org = result.scalar_one_or_none()
    if org is None:
        print(f"ERROR: no organization with slug '{org_slug}' found.", file=sys.stderr)
        sys.exit(1)
    return org.id


async def _resolve_relevant_chunk_ids(db, *, org_id: uuid.UUID, document_titles: list[str]) -> set:
    """Resolves human-readable document titles (from the dataset, which is written before
    chunk IDs exist) to the actual chunk IDs currently in the DB for those documents - so the
    dataset stays readable/maintainable without hardcoding UUIDs that change every re-ingest."""
    if not document_titles:
        return set()
    result = await db.execute(
        select(Document).where(Document.org_id == org_id, Document.title.in_(document_titles))
    )
    documents = result.scalars().all()
    if not documents:
        print(
            f"WARNING: none of {document_titles} found for this org - "
            "has the eval dataset's prerequisite content been uploaded?",
            file=sys.stderr,
        )
        return set()

    doc_ids = [d.id for d in documents]
    chunk_result = await db.execute(select(Chunk.id).where(Chunk.document_id.in_(doc_ids)))
    return set(chunk_result.scalars().all())


async def run_eval(*, org_slug: str, dataset_path: Path) -> EvalRunSummary:
    dataset = json.loads(dataset_path.read_text())
    case_results: list[EvalCaseResult] = []

    async with AsyncSessionLocal() as db:
        org_id = await _resolve_org_id(db, org_slug)

        # A synthetic actor for evaluation purposes - role doesn't matter here since eval
        # only reads, never writes anything requiring elevated permissions.
        actor = Actor(user_id=uuid.uuid4(), org_id=org_id, role="viewer")

        embedding_provider = get_embedding_provider()

        for case in dataset["cases"]:
            print(f"\n--- Case: {case['id']} ---")
            relevant_ids = await _resolve_relevant_chunk_ids(
                db, org_id=org_id, document_titles=case.get("relevant_document_titles", [])
            )

            [query_embedding] = await embedding_provider.embed([case["question"]])
            retrieved = await hybrid_search(
                db, actor=actor, query=case["question"], query_embedding=query_embedding
            )
            retrieved_ids = [c.chunk_id for c in retrieved]

            recall = recall_at_k(retrieved_ids=retrieved_ids, relevant_ids=relevant_ids, k=5)
            mrr = mean_reciprocal_rank(retrieved_ids=retrieved_ids, relevant_ids=relevant_ids)

            # Full end-to-end (retrieval + generation) to evaluate groundedness of the actual
            # answer, not just retrieval quality in isolation.
            conversation = await create_conversation(db, actor=actor)
            answer_result = await answer_question(
                db, actor=actor, conversation_id=conversation.id, question=case["question"]
            )
            groundedness = groundedness_score(
                answer_text=answer_result.answer_text,
                source_texts=[c.content for c in answer_result.citations],
            )

            print(f"  recall@5: {recall:.2f}  MRR: {mrr:.2f}  groundedness: {groundedness.overall_score:.2f}")
            if groundedness.unsupported_sentences:
                print(f"  UNSUPPORTED sentences: {groundedness.unsupported_sentences}")
            if case.get("expect_low_confidence") and not answer_result.low_confidence:
                print("  WARNING: expected low_confidence=True but got False")

            case_results.append(
                EvalCaseResult(
                    case_id=case["id"], recall_at_5=recall, reciprocal_rank=mrr, groundedness=groundedness
                )
            )

    return EvalRunSummary(case_results=case_results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RAG evaluation harness (Sec. 25).")
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--dataset", type=Path, default=Path("eval/sample_dataset.json"))
    args = parser.parse_args()

    summary = asyncio.run(run_eval(org_slug=args.org_slug, dataset_path=args.dataset))

    print("\n=== Summary ===")
    print(f"Mean recall@5:        {summary.mean_recall_at_5:.2f}")
    print(f"Mean reciprocal rank: {summary.mean_reciprocal_rank:.2f}")
    print(f"Mean groundedness:    {summary.mean_groundedness:.2f}")


if __name__ == "__main__":
    main()
