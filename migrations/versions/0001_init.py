"""init: documents, knowledge bases, membership, users/groups, kb access

Revision ID: 0001_init
Revises:
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOC_STATUS = postgresql.ENUM(
    "pending_upload",
    "queued",
    "processing",
    "done",
    "failed",
    "abandoned",
    name="doc_status",
)


def upgrade() -> None:
    bind = op.get_bind()
    DOC_STATUS.create(bind, checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("doc_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(1024), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column(
            "status",
            DOC_STATUS,
            nullable=False,
            server_default="pending_upload",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("content_type", sa.String(255), nullable=False, server_default="application/pdf"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_documents_status", "documents", ["status"])

    op.create_table(
        "knowledge_bases",
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "document_kb",
        sa.Column(
            "doc_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.doc_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "kb_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index("ix_document_kb_kb_id", "document_kb", ["kb_id"])

    op.create_table(
        "users",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "groups",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
    )

    op.create_table(
        "user_groups",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.group_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "kb_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "kb_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.group_id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_kb_access_kb_id", "kb_access", ["kb_id"])
    op.create_index("ix_kb_access_user_id", "kb_access", ["user_id"])
    op.create_index("ix_kb_access_group_id", "kb_access", ["group_id"])


def downgrade() -> None:
    op.drop_table("kb_access")
    op.drop_table("user_groups")
    op.drop_table("groups")
    op.drop_table("users")
    op.drop_table("document_kb")
    op.drop_table("knowledge_bases")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_table("documents")
    DOC_STATUS.drop(op.get_bind(), checkfirst=True)
