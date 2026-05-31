"""Document upload + status routes (handoff §8).

Guiding rule: file bytes never flow through the API. The browser uploads directly
to MinIO via a presigned URL; the API only does the small metadata bookkeeping and
enqueues ingestion on confirmation.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from sherpa.api.deps import current_user
from sherpa.clients import storage
from sherpa.config import settings
from sherpa.db.models import Document, DocStatus, DocumentKB, KnowledgeBase, User
from sherpa.db.session import get_db
from sherpa.schemas import (
    CreateDocumentRequest,
    CreateDocumentResponse,
    DocumentStatusResponse,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=CreateDocumentResponse, status_code=status.HTTP_201_CREATED)
def create_document(
    req: CreateDocumentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),  # noqa: ARG001 — auth gate
) -> CreateDocumentResponse:
    """Step 1-2 of §8: register the doc, return a presigned PUT URL.

    The document officially exists (pending_upload) before a single byte arrives.
    """
    if req.size > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "file too large")

    # Every referenced KB must exist.
    existing = set(
        db.scalars(select(KnowledgeBase.kb_id).where(KnowledgeBase.kb_id.in_(req.kb_ids))).all()
    )
    missing = set(req.kb_ids) - existing
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown kb_ids: {sorted(map(str, missing))}")

    doc = Document(
        filename=req.filename,
        object_key="",  # set below once doc_id exists
        status=DocStatus.pending_upload,
        size_bytes=req.size,
        content_type=req.content_type,
    )
    db.add(doc)
    db.flush()  # assigns doc.doc_id

    key = storage.object_key(str(doc.doc_id))
    doc.object_key = key
    db.add_all([DocumentKB(doc_id=doc.doc_id, kb_id=kb_id) for kb_id in req.kb_ids])

    upload_url = storage.presigned_put_url(key, req.content_type)
    db.commit()

    return CreateDocumentResponse(doc_id=doc.doc_id, upload_url=upload_url, object_key=key)


@router.post("/{doc_id}/complete", response_model=DocumentStatusResponse)
def complete_upload(
    doc_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),  # noqa: ARG001 — auth gate
) -> DocumentStatusResponse:
    """Step 3-4 of §8: browser confirms the upload; flip to queued + enqueue."""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown doc_id")
    if doc.status not in (DocStatus.pending_upload, DocStatus.failed, DocStatus.abandoned):
        # Already queued/processing/done — idempotent no-op confirmation.
        return _status_response(db, doc)

    doc.status = DocStatus.queued
    db.commit()

    # Imported here so the API image never imports the ingestion-heavy task module
    # at startup; the call only needs the Celery signature to enqueue.
    from sherpa.worker.tasks import ingest_document

    ingest_document.apply_async(args=[str(doc_id)], queue="default")
    return _status_response(db, doc)


@router.get("/{doc_id}", response_model=DocumentStatusResponse)
def get_document(
    doc_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),  # noqa: ARG001 — auth gate
) -> DocumentStatusResponse:
    """Status polling — Postgres status is the single source of truth (§8)."""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown doc_id")
    return _status_response(db, doc)


def _status_response(db: Session, doc: Document) -> DocumentStatusResponse:
    kb_ids = db.scalars(select(DocumentKB.kb_id).where(DocumentKB.doc_id == doc.doc_id)).all()
    return DocumentStatusResponse(
        doc_id=doc.doc_id,
        filename=doc.filename,
        status=doc.status.value,
        page_count=doc.page_count,
        error_message=doc.error_message,
        kb_ids=list(kb_ids),
    )
