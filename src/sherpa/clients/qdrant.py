"""Qdrant client — the searchable projection of the documents (handoff §6, §7).

Responsibilities:
  * bootstrap the single collection + payload indexes (idempotent)
  * deterministic point IDs for idempotent ingestion
  * delete-then-write per document
  * filtered nearest-neighbour search (access control)

Only vectors + the per-chunk payload live here; it is rebuildable from MinIO.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from sherpa.config import settings

# Stable namespace so chunk point IDs are deterministic across runs/processes.
# Qdrant point IDs must be int or UUID — NOT the bare string "{doc_id}_{idx}".
# A UUIDv5 over that string is both deterministic (idempotency, §7 Fix 1) and a
# legal Qdrant ID.
_NAMESPACE_SHERPA = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def point_id(doc_id: str, chunk_index: int) -> str:
    """Deterministic point id: same doc + same chunk index -> same id, always."""
    return str(uuid.uuid5(_NAMESPACE_SHERPA, f"{doc_id}_{chunk_index}"))


@dataclass
class ChunkPoint:
    chunk_index: int
    vector: list[float]
    doc_id: str
    kb_ids: list[str]
    filename: str
    page: int
    snippet: str


def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create the collection + payload indexes if absent. Safe to call repeatedly."""
    client = client or get_client()

    if not client.collection_exists(settings.collection_name):
        client.create_collection(
            collection_name=settings.collection_name,
            vectors_config=qm.VectorParams(
                size=settings.embed_dim,
                distance=qm.Distance.COSINE,
            ),
        )

    # Payload indexes on the two fields we filter on (§6). Display-only fields
    # (filename, page, snippet) are intentionally NOT indexed.
    for field in ("doc_id", "kb_id"):
        try:
            client.create_payload_index(
                collection_name=settings.collection_name,
                field_name=field,
                field_schema=qm.PayloadSchemaType.KEYWORD,
            )
        except Exception:
            # Index already exists — Qdrant raises; treat as idempotent no-op.
            pass


def delete_document(doc_id: str, client: QdrantClient | None = None) -> None:
    """Delete every point for a document: DELETE ... WHERE doc_id = X (§7 Fix 2)."""
    client = client or get_client()
    client.delete(
        collection_name=settings.collection_name,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
            )
        ),
        wait=True,
    )


def upsert_chunks(chunks: list[ChunkPoint], client: QdrantClient | None = None) -> None:
    """Write (or overwrite) chunk points by deterministic id."""
    if not chunks:
        return
    client = client or get_client()
    points = [
        qm.PointStruct(
            id=point_id(c.doc_id, c.chunk_index),
            vector=c.vector,
            payload={
                "doc_id": c.doc_id,
                "kb_id": c.kb_ids,  # stored as a list (§6)
                "filename": c.filename,
                "page": c.page,
                "snippet": c.snippet,
            },
        )
        for c in chunks
    ]
    client.upsert(collection_name=settings.collection_name, points=points, wait=True)


def count_document(doc_id: str, client: QdrantClient | None = None) -> int:
    """How many points exist for a doc (used by verification/tests)."""
    client = client or get_client()
    return client.count(
        collection_name=settings.collection_name,
        count_filter=qm.Filter(
            must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))]
        ),
        exact=True,
    ).count


def search(
    vector: list[float],
    allowed_kb_ids: list[str],
    top_k: int,
    client: QdrantClient | None = None,
) -> list[qm.ScoredPoint]:
    """Nearest neighbours WHERE kb_id IN allowed_kb_ids (access control, §6).

    `allowed_kb_ids` is the *resolved* set from Postgres. An empty list means the
    user may see nothing; we return [] without querying.
    """
    if not allowed_kb_ids:
        return []
    client = client or get_client()
    return client.search(
        collection_name=settings.collection_name,
        query_vector=vector,
        query_filter=qm.Filter(
            must=[qm.FieldCondition(key="kb_id", match=qm.MatchAny(any=allowed_kb_ids))]
        ),
        limit=top_k,
        with_payload=True,
    )
