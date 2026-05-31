"""Pydantic request/response models for the API.

Kept deliberately clean and agent-ready: /search returns structured JSON with
relevance scores exposed and a caller-controlled top_k (handoff §6).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Documents / upload flow
# ---------------------------------------------------------------------------


class CreateDocumentRequest(BaseModel):
    filename: str
    size: int = Field(ge=0)
    content_type: str = "application/pdf"
    kb_ids: list[UUID] = Field(min_length=1)


class CreateDocumentResponse(BaseModel):
    doc_id: UUID
    upload_url: str
    object_key: str


class DocumentStatusResponse(BaseModel):
    doc_id: UUID
    filename: str
    status: str
    page_count: int | None = None
    error_message: str | None = None
    kb_ids: list[UUID] = []


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    # Optional caller-side narrowing; always intersected with the user's allowed KBs.
    kb_ids: list[UUID] | None = None


class SearchHit(BaseModel):
    doc_id: UUID
    filename: str
    page: int
    snippet: str
    score: float


class SearchResponse(BaseModel):
    hits: list[SearchHit]
