"""Celery tasks: ingestion + abandoned-upload sweep (handoff §7, §8).

The pipeline (ingestion/pipeline.py) is idempotent, so the queue's job here is to
make retries *sensible* and failures *visible*:

  * bounded retries for transient errors (TEI/network/MinIO blips)
  * permanent errors (corrupt/encrypted PDF) skip retries entirely
  * either way, an exhausted/permanent failure -> Postgres status=failed AND a
    message on the dead_letter queue (never silently dropped)
  * per-document status in Postgres makes a half-failed ingest observable
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

import httpx
from celery.exceptions import MaxRetriesExceededError
from celery.utils.log import get_task_logger
from sqlalchemy import select

from sherpa.clients import storage
from sherpa.clients.tei import TEIError
from sherpa.config import settings
from sherpa.db.models import Document, DocStatus, DocumentKB
from sherpa.db.session import session_scope
from sherpa.ingestion.extract import PermanentExtractError
from sherpa.ingestion.pipeline import ingest_pdf
from sherpa.worker.celery_app import celery_app

log = get_task_logger(__name__)

# Errors worth retrying (transient infra). Everything else is treated as permanent.
TRANSIENT_ERRORS = (httpx.HTTPError, TEIError, ConnectionError, TimeoutError)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _set_status(
    doc_id: str,
    status: DocStatus,
    *,
    error: str | None = None,
    page_count: int | None = None,
) -> None:
    with session_scope() as s:
        doc = s.get(Document, UUID(doc_id))
        if doc is None:
            log.warning("doc %s vanished while setting status=%s", doc_id, status)
            return
        doc.status = status
        if error is not None:
            doc.error_message = error[:2000]
        if page_count is not None:
            doc.page_count = page_count


def _load_doc(doc_id: str) -> tuple[str, str, list[str]] | None:
    """Return (filename, object_key, kb_ids) or None if the row is gone."""
    with session_scope() as s:
        doc = s.get(Document, UUID(doc_id))
        if doc is None:
            return None
        kb_ids = s.scalars(
            select(DocumentKB.kb_id).where(DocumentKB.doc_id == UUID(doc_id))
        ).all()
        return doc.filename, doc.object_key, [str(k) for k in kb_ids]


def _dead_letter(doc_id: str, reason: str) -> None:
    """Mark failed and park a message on the dead_letter queue for a human."""
    _set_status(doc_id, DocStatus.failed, error=reason)
    record_dead_letter.apply_async(args=[doc_id, reason], queue="dead_letter")


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #
@celery_app.task(
    bind=True,
    name="sherpa.worker.tasks.ingest_document",
    max_retries=settings.max_ingest_retries,
    default_retry_delay=10,
)
def ingest_document(self, doc_id: str) -> dict:
    """Ingest one document end-to-end. Safe to run again (idempotent pipeline)."""
    loaded = _load_doc(doc_id)
    if loaded is None:
        log.warning("ingest_document: doc %s not found; skipping", doc_id)
        return {"doc_id": doc_id, "skipped": "not_found"}
    filename, object_key, kb_ids = loaded

    _set_status(doc_id, DocStatus.processing)

    try:
        pdf_bytes = storage.get_bytes(object_key)
        result = ingest_pdf(doc_id, kb_ids, filename, pdf_bytes)

    except PermanentExtractError as exc:
        # Corrupt / not a PDF / password-protected — retrying can never help.
        log.error("permanent failure for %s: %s", doc_id, exc)
        _dead_letter(doc_id, f"permanent: {exc}")
        return {"doc_id": doc_id, "status": "failed", "reason": str(exc)}

    except TRANSIENT_ERRORS as exc:
        # Transient infra blip — back off and retry, up to max_retries.
        try:
            backoff = 10 * (2**self.request.retries)  # 10s, 20s, 40s ...
            raise self.retry(exc=exc, countdown=backoff)
        except MaxRetriesExceededError:
            log.error("retries exhausted for %s: %s", doc_id, exc)
            _dead_letter(doc_id, f"transient, retries exhausted: {exc}")
            return {"doc_id": doc_id, "status": "failed", "reason": str(exc)}

    _set_status(doc_id, DocStatus.done, page_count=result.page_count)
    return {
        "doc_id": doc_id,
        "status": "done",
        "pages": result.page_count,
        "chunks": result.chunk_count,
    }


@celery_app.task(name="sherpa.worker.tasks.record_dead_letter", queue="dead_letter")
def record_dead_letter(doc_id: str, reason: str) -> dict:
    """Lands on the dead_letter queue. Presence here = needs a human (§7)."""
    log.error("DEAD-LETTER doc=%s reason=%s", doc_id, reason)
    return {"doc_id": doc_id, "reason": reason}


@celery_app.task(name="sherpa.worker.tasks.sweep_abandoned_uploads")
def sweep_abandoned_uploads() -> dict:
    """Recover/clean uploads stuck in pending_upload (handoff §8).

    For each pending_upload older than abandoned_after_s: if the object actually
    landed in MinIO -> enqueue ingest (recovers a browser that died before
    confirming); otherwise -> mark abandoned.
    """
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=settings.abandoned_after_s)
    recovered = 0
    abandoned = 0

    with session_scope() as s:
        stale = s.scalars(
            select(Document).where(
                Document.status == DocStatus.pending_upload,
                Document.created_at < cutoff,
            )
        ).all()
        candidates = [(str(d.doc_id), d.object_key) for d in stale]

    for doc_id, object_key in candidates:
        if storage.object_exists(object_key):
            _set_status(doc_id, DocStatus.queued)
            ingest_document.apply_async(args=[doc_id], queue="default")
            recovered += 1
        else:
            _set_status(doc_id, DocStatus.abandoned)
            abandoned += 1

    if candidates:
        log.info("sweep: recovered=%d abandoned=%d", recovered, abandoned)
    return {"checked": len(candidates), "recovered": recovered, "abandoned": abandoned}
