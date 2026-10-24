"""Кейсы модерации (5c, поток F4): проекция аватарок и аудит ручных действий.

Только новые таблицы (expand).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "avatar_proj",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('approved', 'rejected')", name=op.f("ck_avatar_proj_status_known")
        ),
        sa.PrimaryKeyConstraint("asset_id", name=op.f("pk_avatar_proj")),
    )
    op.create_table(
        "moderation_audit",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_moderation_audit")),
    )
    op.create_index(
        "ix_moderation_audit_target_at", "moderation_audit", ["target", "at"], unique=False
    )
    op.create_index(
        "ix_moderation_audit_actor_at", "moderation_audit", ["actor_id", "at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_moderation_audit_actor_at", table_name="moderation_audit")
    op.drop_index("ix_moderation_audit_target_at", table_name="moderation_audit")
    op.drop_table("moderation_audit")
    op.drop_table("avatar_proj")
