"""Санкции и версия набора санкций игрока (5c, поток F3; ADR 0052).

Только новые таблицы (expand).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "sanctions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "kind IN ('ranked', 'chat', 'online', 'account')",
            name=op.f("ck_sanctions_kind_known"),
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > started_at",
            name=op.f("ck_sanctions_expires_after_start"),
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoke_reason IS NULL)",
            name=op.f("ck_sanctions_revoked_pair"),
        ),
        sa.CheckConstraint(
            "revoked_by IS NULL OR revoked_at IS NOT NULL",
            name=op.f("ck_sanctions_revoked_by_needs_revoke"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sanctions")),
    )
    op.create_index(
        "uq_sanctions_open_kind",
        "sanctions",
        ["user_id", "kind"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_sanctions_user_started", "sanctions", ["user_id", sa.text("started_at DESC")]
    )
    op.create_index(
        "ix_sanctions_open_expires",
        "sanctions",
        ["expires_at"],
        postgresql_where=sa.text("revoked_at IS NULL AND expires_at IS NOT NULL"),
    )
    op.create_table(
        "sanction_state",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_sanction_state_version_positive")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_sanction_state")),
    )


def downgrade() -> None:
    op.drop_table("sanction_state")
    op.drop_index(
        "ix_sanctions_open_expires",
        table_name="sanctions",
        postgresql_where=sa.text("revoked_at IS NULL AND expires_at IS NOT NULL"),
    )
    op.drop_index("ix_sanctions_user_started", table_name="sanctions")
    op.drop_index(
        "uq_sanctions_open_kind",
        table_name="sanctions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("sanctions")
