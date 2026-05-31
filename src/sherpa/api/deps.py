"""Request dependencies — including the auth seam (handoff §5, §6 deferred note).

DEV AUTH ONLY: the current user is read from an `X-User-Id` header. Real
authentication (sessions / JWT) replaces exactly this one function later; nothing
downstream changes. The header value must be a real users.user_id.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from sherpa.db.models import User
from sherpa.db.session import get_db


def current_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    db: Session = Depends(get_db),
) -> User:
    if not x_user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing X-User-Id header")
    try:
        uid = UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "X-User-Id is not a valid UUID") from exc

    user = db.get(User, uid)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return user
