"""SQLAlchemy 2.0 ORM models — the system of record (handoff §6).

Postgres holds the *facts* about each document (status, ownership, KB membership,
permissions). Qdrant holds the searchable chunks (derived, rebuildable). MinIO
holds the bytes. Nothing here stores vectors.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DocStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"
    abandoned = "abandoned"


def _uuid_col(primary_key: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        PGUUID(as_uuid=True),
        primary_key=primary_key,
        default=uuid.uuid4 if primary_key else None,
    )


class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(1024))
    object_key: Mapped[str] = mapped_column(String(1024))
    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus, name="doc_status"),
        default=DocStatus.pending_upload,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    content_type: Mapped[str] = mapped_column(String(255), default="application/pdf")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    kbs: Mapped[list[DocumentKB]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    kb_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DocumentKB(Base):
    """Document <-> KB membership (many-to-many; mirrors kb_id-as-a-list)."""

    __tablename__ = "document_kb"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.doc_id", ondelete="CASCADE"),
        primary_key=True,
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"),
        primary_key=True,
    )

    document: Mapped[Document] = relationship(back_populates="kbs")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Group(Base):
    __tablename__ = "groups"

    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True)


class UserGroup(Base):
    __tablename__ = "user_groups"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("groups.group_id", ondelete="CASCADE"),
        primary_key=True,
    )


class KBAccess(Base):
    """Grants a KB to a user OR a group. Source of truth for permissions.

    Exactly one of (user_id, group_id) is set per row. Qdrant only ever receives
    the *resolved* set of allowed kb_ids — it never reads this table.
    """

    __tablename__ = "kb_access"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kb_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    group_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("groups.group_id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
