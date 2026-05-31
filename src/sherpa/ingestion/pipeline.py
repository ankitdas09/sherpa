"""Ingestion orchestration (handoff §3, §7).

Pure pipeline: given a document's bytes + identity, produce the correct set of
Qdrant points. Designed so running it again yields the same final state:

    1. extract (PyMuPDF + OCR fallback)
    2. chunk   (~450 tokens, page-tracked)
    3. embed   (via the shared TEI service)
    4. delete-then-write by doc_id (deterministic point ids)

Postgres status transitions and retry/dead-letter policy live in the Celery task
(worker/tasks.py); this module stays free of queue concerns so it's unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from sherpa.clients import qdrant, tei
from sherpa.ingestion.chunk import chunk_pages
from sherpa.ingestion.extract import extract_pages


@dataclass
class IngestResult:
    page_count: int
    chunk_count: int


def ingest_pdf(
    doc_id: str,
    kb_ids: list[str],
    filename: str,
    pdf_bytes: bytes,
) -> IngestResult:
    """Run the full pipeline for one document and write its chunks to Qdrant.

    Idempotent at the Qdrant layer: delete-by-doc wipes any partial/old state,
    then deterministic ids mean a re-run converges to exactly the right points
    (covers both a half-failed retry and a changed chunk count on re-ingest).
    """
    pages = extract_pages(pdf_bytes)
    chunks = chunk_pages(pages)

    # delete-then-write, even when there are zero chunks (e.g. truly empty PDF):
    # the delete clears any stale points from a prior version of this doc.
    client = qdrant.get_client()
    qdrant.delete_document(doc_id, client=client)

    if chunks:
        vectors = tei.embed_texts([c.text for c in chunks])
        points = [
            qdrant.ChunkPoint(
                chunk_index=c.index,
                vector=vectors[i],
                doc_id=doc_id,
                kb_ids=kb_ids,
                filename=filename,
                page=c.page,
                snippet=c.text,
            )
            for i, c in enumerate(chunks)
        ]
        qdrant.upsert_chunks(points, client=client)

    return IngestResult(page_count=len(pages), chunk_count=len(chunks))
