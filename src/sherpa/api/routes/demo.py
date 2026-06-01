"""Developer visualizer backend (NOT the user-facing app).

Each endpoint runs the REAL backend operations — real Postgres rows, real MinIO
objects, real TEI embeddings, real Qdrant points — but orchestrated synchronously
from one place so it can return a step-by-step trace of what happened and which
components talked to which. The frontend (static/index.html) animates that trace.

Honesty note: in production, steps after "enqueue" run in a separate Celery worker
pulled off Redis. The demo executes them inline so it can narrate each stage; the
trace labels them as the worker's work and still performs the real I/O.
"""

from __future__ import annotations

import textwrap
import time
from dataclasses import dataclass, field
from typing import Any

import fitz  # PyMuPDF
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from sherpa.access import resolve_allowed_kb_ids
from sherpa.clients import qdrant, storage, tei
from sherpa.config import settings
from sherpa.db.models import (
    Document,
    DocStatus,
    DocumentKB,
    KBAccess,
    KnowledgeBase,
    User,
)
from sherpa.db.session import session_scope
from sherpa.ingestion.chunk import chunk_pages
from sherpa.ingestion.extract import extract_pages

router = APIRouter(prefix="/demo", tags=["demo"])

DEMO_KB_NAME = "default"
ALICE = "alice@example.com"
BOB = "bob@example.com"

DEFAULT_TEXT = (
    "Coral reefs are among the most biodiverse ecosystems on the planet.\n"
    "They are built by colonies of tiny animals called polyps that secrete\n"
    "calcium carbonate skeletons. Rising ocean temperatures cause the symbiotic\n"
    "algae to be expelled, a process known as coral bleaching that can lead to\n"
    "widespread reef death if the heat stress is prolonged."
)


# --------------------------------------------------------------------------- #
# step trace
# --------------------------------------------------------------------------- #
@dataclass
class Flow:
    steps: list[dict[str, Any]] = field(default_factory=list)

    def add(
        self,
        frm: str,
        to: str,
        title: str,
        detail: dict | None = None,
        ms: float | None = None,
        kind: str = "info",
    ) -> None:
        self.steps.append(
            {
                "from": frm,
                "to": to,
                "title": title,
                "detail": detail or {},
                "ms": round(ms, 1) if ms is not None else None,
                "kind": kind,
            }
        )


class _Timer:
    def __enter__(self):
        self.t = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.ms = (time.perf_counter() - self.t) * 1000


# --------------------------------------------------------------------------- #
# requests
# --------------------------------------------------------------------------- #
class IngestRequest(BaseModel):
    text: str | None = None
    filename: str | None = None


class SearchRequest(BaseModel):
    query: str
    as_user: str = "alice"  # "alice" (has access) or "bob" (denied)
    top_k: int = 5


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _ensure_entities(s) -> tuple[KnowledgeBase, User, User]:
    kb = s.scalar(select(KnowledgeBase).where(KnowledgeBase.name == DEMO_KB_NAME))
    if kb is None:
        kb = KnowledgeBase(name=DEMO_KB_NAME, description="Demo knowledge base")
        s.add(kb)
        s.flush()
    users = {}
    for email in (ALICE, BOB):
        u = s.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email)
            s.add(u)
            s.flush()
        users[email] = u
    # grant alice only
    if not s.scalar(
        select(KBAccess).where(KBAccess.kb_id == kb.kb_id, KBAccess.user_id == users[ALICE].user_id)
    ):
        s.add(KBAccess(kb_id=kb.kb_id, user_id=users[ALICE].user_id))
        s.flush()
    return kb, users[ALICE], users[BOB]


def _make_pdf(text: str) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for para in text.split("\n"):
        for line in textwrap.wrap(para, 90) or [""]:
            page.insert_text((72, y), line, fontsize=11)
            y += 16
    data = doc.tobytes()
    doc.close()
    return data


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
@router.get("/state")
def state() -> dict:
    """Current system snapshot for the UI's stat panel."""
    with session_scope() as s:
        kb, alice, bob = _ensure_entities(s)
        rows = s.execute(
            select(Document.status, func.count()).group_by(Document.status)
        ).all()
        by_status = {st.value: n for st, n in rows}
        total = sum(by_status.values())
        ids = {
            "kb_id": str(kb.kb_id),
            "alice_id": str(alice.user_id),
            "bob_id": str(bob.user_id),
        }
    try:
        points = qdrant.get_client().count(settings.collection_name, exact=True).count
    except Exception:
        points = None
    return {
        "documents_total": total,
        "documents_by_status": by_status,
        "qdrant_points": points,
        "collection": settings.collection_name,
        "embed_dim": settings.embed_dim,
        **ids,
    }


@router.post("/ingest")
def demo_ingest(req: IngestRequest) -> dict:
    """Run the full upload → ingest flow and return its step trace."""
    flow = Flow()
    text = (req.text or DEFAULT_TEXT).strip()
    filename = req.filename or "demo.pdf"
    pdf = _make_pdf(text)

    flow.add(
        "browser",
        "api",
        "POST /documents (metadata only)",
        {"filename": filename, "size_bytes": len(pdf), "note": "file bytes do NOT go through the API"},
        kind="request",
    )

    with session_scope() as s:
        kb, _, _ = _ensure_entities(s)
        doc = Document(
            filename=filename,
            object_key="",
            status=DocStatus.pending_upload,
            size_bytes=len(pdf),
            content_type="application/pdf",
        )
        s.add(doc)
        s.flush()
        doc_id = str(doc.doc_id)
        key = storage.object_key(doc_id)
        doc.object_key = key
        s.add(DocumentKB(doc_id=doc.doc_id, kb_id=kb.kb_id))
        kb_id = str(kb.kb_id)

    flow.add("api", "postgres", "INSERT documents (status=pending_upload)",
             {"doc_id": doc_id, "kb_id": kb_id}, kind="db")

    with _Timer() as t:
        url = storage.presigned_put_url(key, "application/pdf")
    flow.add("api", "minio", "Generate presigned PUT URL",
             {"object_key": key, "url": url[:88] + "…"}, ms=t.ms, kind="store")
    flow.add("api", "browser", "Return doc_id + upload_url", {"doc_id": doc_id}, kind="response")

    with _Timer() as t:
        storage.put_bytes(key, pdf, "application/pdf")
    flow.add("browser", "minio", "PUT bytes directly to MinIO",
             {"bytes": len(pdf), "http": 200, "note": "browser → MinIO, bypassing the API"},
             ms=t.ms, kind="store")

    flow.add("browser", "api", "POST /documents/{id}/complete", {"doc_id": doc_id}, kind="request")
    with session_scope() as s:
        s.get(Document, doc.doc_id).status = DocStatus.queued
    flow.add("api", "postgres", "UPDATE status=queued", {"doc_id": doc_id}, kind="db")
    flow.add("api", "redis", "Enqueue ingest_document(doc_id)",
             {"queue": "default", "doc_id": doc_id}, kind="queue")

    # ---- worker side (run inline; labelled as the worker's job) ----
    flow.add("redis", "worker", "Worker pulls job from queue", {"queue": "default"}, kind="queue")
    with session_scope() as s:
        s.get(Document, doc.doc_id).status = DocStatus.processing
    flow.add("worker", "postgres", "UPDATE status=processing", {"doc_id": doc_id}, kind="db")

    with _Timer() as t:
        pdf_bytes = storage.get_bytes(key)
    flow.add("worker", "minio", "GET pdf bytes", {"bytes": len(pdf_bytes)}, ms=t.ms, kind="store")

    with _Timer() as t:
        pages = extract_pages(pdf_bytes)
    flow.add("worker", "worker", "Extract text (PyMuPDF + OCR fallback)",
             {"pages": len(pages), "chars": sum(len(p.text) for p in pages)},
             ms=t.ms, kind="compute")

    with _Timer() as t:
        chunks = chunk_pages(pages)
    flow.add("worker", "worker",
             f"Chunk (~{settings.chunk_tokens} tok / {settings.chunk_overlap_tokens} overlap)",
             {"chunks": len(chunks),
              "preview": (chunks[0].text[:140] + "…") if chunks else ""},
             ms=t.ms, kind="compute")

    with _Timer() as t:
        vectors = tei.embed_texts([c.text for c in chunks]) if chunks else []
    flow.add("worker", "tei", "Embed chunks (the only model)",
             {"vectors": len(vectors), "dim": len(vectors[0]) if vectors else 0},
             ms=t.ms, kind="model")

    with _Timer() as t:
        client = qdrant.get_client()
        qdrant.delete_document(doc_id, client=client)
        if chunks:
            points = [
                qdrant.ChunkPoint(
                    chunk_index=c.index, vector=vectors[i], doc_id=doc_id,
                    kb_ids=[kb_id], filename=filename, page=c.page, snippet=c.text,
                )
                for i, c in enumerate(chunks)
            ]
            qdrant.upsert_chunks(points, client=client)
    flow.add("worker", "qdrant", "Delete-by-doc, then upsert points",
             {"points": len(chunks),
              "ids": [qdrant.point_id(doc_id, c.index) for c in chunks[:3]],
              "note": "deterministic ids → idempotent"},
             ms=t.ms, kind="store")

    with session_scope() as s:
        d = s.get(Document, doc.doc_id)
        d.status = DocStatus.done
        d.page_count = len(pages)
    flow.add("worker", "postgres", "UPDATE status=done, page_count",
             {"doc_id": doc_id, "page_count": len(pages)}, kind="db")

    return {"doc_id": doc_id, "chunks": len(chunks), "pages": len(pages), "steps": flow.steps}


@router.post("/search")
def demo_search(req: SearchRequest) -> dict:
    """Run the query flow and return its step trace (incl. the access filter)."""
    flow = Flow()
    email = ALICE if req.as_user == "alice" else BOB
    flow.add("browser", "api", "POST /search",
             {"query": req.query, "as": req.as_user, "top_k": req.top_k}, kind="request")

    with session_scope() as s:
        _, alice, bob = _ensure_entities(s)
        user = alice if email == ALICE else bob
        allowed = resolve_allowed_kb_ids(s, user.user_id)
    allowed_list = sorted(str(k) for k in allowed)
    flow.add("api", "postgres", "Resolve allowed kb_ids (kb_access + groups)",
             {"user": req.as_user, "allowed_kb_ids": allowed_list}, kind="db")

    if not allowed:
        flow.add("api", "browser", "Access set empty → short-circuit, return []",
                 {"hits": 0, "note": "Qdrant is never queried"}, kind="response")
        return {"hits": [], "steps": flow.steps}

    with _Timer() as t:
        vector = tei.embed_query(req.query)
    flow.add("api", "tei", "Embed query (same model as ingestion)",
             {"dim": len(vector)}, ms=t.ms, kind="model")

    with _Timer() as t:
        points = qdrant.search(vector, allowed_kb_ids=allowed_list, top_k=req.top_k)
    hits = [
        {
            "doc_id": p.payload["doc_id"],
            "filename": p.payload["filename"],
            "page": p.payload["page"],
            "score": round(p.score, 4),
            "snippet": p.payload["snippet"][:160],
        }
        for p in points
    ]
    flow.add("api", "qdrant", "Nearest neighbours WHERE kb_id ∈ allowed",
             {"filter_kb_ids": allowed_list, "returned": len(hits)}, ms=t.ms, kind="store")
    flow.add("api", "browser", "Return ranked hits", {"hits": len(hits)}, kind="response")

    return {"hits": hits, "steps": flow.steps}


@router.post("/reset")
def demo_reset() -> dict:
    """Wipe all demo documents: Qdrant points, MinIO objects, Postgres rows."""
    client = qdrant.get_client()
    if client.collection_exists(settings.collection_name):
        client.delete_collection(settings.collection_name)
    qdrant.ensure_collection(client)
    removed_objects = storage.clear_uploads()
    with session_scope() as s:
        s.query(DocumentKB).delete()
        n = s.query(Document).delete()
    return {"deleted_documents": n, "deleted_objects": removed_objects, "qdrant": "recreated"}
