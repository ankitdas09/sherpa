"""Synchronous SQLAlchemy engine + session factory.

Sync (not async) on purpose: the Celery workers are sync, and the API's DB work
is light metadata. One engine module shared by api, worker, and scripts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sherpa.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Caller (route) controls commit/rollback explicitly."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
