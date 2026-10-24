"""Комментарии, жалобы, кейсы модерации, проекции и счётчики порядочности (5c, поток F2).

Только новые таблицы (expand).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_KINDS = "target_kind IN ('player', 'collection', 'comment', 'avatar')"


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("edited_at", _TS, nullable=True),
        sa.Column("deleted_at", _TS, nullable=True),
        sa.Column("deleted_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_role", sa.String(length=32), nullable=True),
        sa.CheckConstraint("char_length(body) <= 500", name=op.f("ck_comments_body_max_length")),
        sa.CheckConstraint(
            "deleted_role IS NULL OR deleted_role IN "
            "('author', 'collection_owner', 'moderator', 'account_deleted')",
            name=op.f("ck_comments_deleted_role_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(
        "ix_comments_collection_created",
        "comments",
        ["collection_id", sa.text("created_at DESC"), "id"],
        unique=False,
    )
    op.create_index("ix_comments_author", "comments", ["author_id"], unique=False)
    op.create_table(
        "collections_proj",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=120), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('published', 'unpublished')", name=op.f("ck_collections_proj_status_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collections_proj")),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("context_match_id", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(_KINDS, name=op.f("ck_reports_target_kind_known")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.UniqueConstraint(
            "author_id", "target_kind", "target_id", name="uq_reports_author_target"
        ),
    )
    op.create_index(
        "ix_reports_target_created",
        "reports",
        ["target_kind", "target_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_reports_author_created", "reports", ["author_id", "created_at"])
    op.create_table(
        "moderation_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("opened_at", _TS, nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", _TS, nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(_KINDS, name=op.f("ck_moderation_cases_target_kind_known")),
        sa.CheckConstraint(
            "status IN ('open', 'in_work', 'escalated', 'resolved')",
            name=op.f("ck_moderation_cases_status_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_cases")),
    )
    op.create_index(
        "uq_moderation_cases_open_target",
        "moderation_cases",
        ["target_kind", "target_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.create_index("ix_moderation_cases_queue", "moderation_cases", ["status", "opened_at", "id"])
    op.create_index(
        "ix_moderation_cases_target_user", "moderation_cases", ["target_user_id"], unique=False
    )
    op.create_table(
        "case_reports",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["moderation_cases.id"],
            name=op.f("fk_case_reports_case_id_moderation_cases"),
        ),
        sa.ForeignKeyConstraint(
            ["report_id"], ["reports.id"], name=op.f("fk_case_reports_report_id_reports")
        ),
        sa.PrimaryKeyConstraint("case_id", "report_id", name=op.f("pk_case_reports")),
    )
    op.create_index("ix_case_reports_report", "case_reports", ["report_id"], unique=False)
    op.create_table(
        "match_participants_proj",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("solo", sa.Boolean(), nullable=False),
        sa.Column("finished_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("match_id", "user_id", name=op.f("pk_match_participants_proj")),
    )
    op.create_index(
        "ix_match_participants_user", "match_participants_proj", ["user_id"], unique=False
    )
    op.create_table(
        "behaviour_counters",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("forfeits", sa.Integer(), server_default="0", nullable=False),
        sa.Column("leaves", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tech_losses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reports_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("praises", sa.Integer(), server_default="0", nullable=False),
        sa.Column("matches_total", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_behaviour_counters")),
    )
    op.create_table(
        "comment_idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name=op.f("ck_comment_idempotency_keys_request_hash_sha256"),
        ),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299",
            name=op.f("ck_comment_idempotency_keys_status_code_success"),
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_comment_idempotency_keys")),
    )
    op.create_index(
        "ix_comment_idempotency_keys_created_at",
        "comment_idempotency_keys",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_comment_idempotency_keys_created_at", table_name="comment_idempotency_keys")
    op.drop_table("comment_idempotency_keys")
    op.drop_table("behaviour_counters")
    op.drop_index("ix_match_participants_user", table_name="match_participants_proj")
    op.drop_table("match_participants_proj")
    op.drop_index("ix_case_reports_report", table_name="case_reports")
    op.drop_table("case_reports")
    op.drop_index("ix_moderation_cases_target_user", table_name="moderation_cases")
    op.drop_index("ix_moderation_cases_queue", table_name="moderation_cases")
    op.drop_index(
        "uq_moderation_cases_open_target",
        table_name="moderation_cases",
        postgresql_where=sa.text("status <> 'resolved'"),
    )
    op.drop_table("moderation_cases")
    op.drop_index("ix_reports_author_created", table_name="reports")
    op.drop_index("ix_reports_target_created", table_name="reports")
    op.drop_table("reports")
    op.drop_table("collections_proj")
    op.drop_index("ix_comments_author", table_name="comments")
    op.drop_index("ix_comments_collection_created", table_name="comments")
    op.drop_table("comments")
