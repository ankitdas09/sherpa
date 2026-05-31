"""Semantic search route (handoff §3, §6).

Stateless: the API embeds the query via the shared TEI service (same model as
ingestion), resolves the user's allowed KBs from Postgres, and asks Qdrant for
nearest neighbours within that filter. No LLM, no generation — raw passages with
scores. Response is clean structured JSON (agent-ready).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sherpa.access import resolve_allowed_kb_ids
from sherpa.api.deps import current_user
from sherpa.clients import qdrant, tei
from sherpa.db.models import User
from sherpa.db.session import get_db
from sherpa.schemas import SearchHit, SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
def search(
    req: SearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> SearchResponse:
    # 1. Resolve what this user is allowed to see (source of truth: Postgres).
    allowed = resolve_allowed_kb_ids(db, user.user_id)

    # 2. Optional caller-side narrowing, always intersected with the allowed set
    #    so a request can never widen access.
    if req.kb_ids is not None:
        allowed &= set(req.kb_ids)

    if not allowed:
        return SearchResponse(hits=[])

    # 3. Embed the query with the SAME model as ingestion (the only model).
    vector = tei.embed_query(req.query)

    # 4. Nearest neighbours WHERE kb_id IN allowed.
    points = qdrant.search(
        vector=vector,
        allowed_kb_ids=[str(k) for k in allowed],
        top_k=req.top_k,
    )

    hits = [
        SearchHit(
            doc_id=p.payload["doc_id"],
            filename=p.payload["filename"],
            page=p.payload["page"],
            snippet=p.payload["snippet"],
            score=p.score,
        )
        for p in points
    ]
    return SearchResponse(hits=hits)
