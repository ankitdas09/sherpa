"""Access control resolution (handoff §6).

The source of truth for who-sees-what lives here in SQL, not in Qdrant. At query
time we resolve "which KBs may this user see" into a concrete set of kb_ids, then
hand Qdrant only that resolved filter. Qdrant stays dumb and fast.

Per-KB by default. If finer (per-document) access is ever needed, it's the same
mechanism resolving a different allowed set — the vector storage never changes.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from sherpa.db.models import KBAccess, UserGroup


def resolve_allowed_kb_ids(session: Session, user_id: UUID) -> set[UUID]:
    """All kb_ids this user may see: direct grants + grants to their groups."""
    group_ids = session.scalars(
        select(UserGroup.group_id).where(UserGroup.user_id == user_id)
    ).all()

    stmt = select(KBAccess.kb_id).where(
        (KBAccess.user_id == user_id)
        | (KBAccess.group_id.in_(group_ids) if group_ids else False)
    )
    return set(session.scalars(stmt).all())
