"""Санкции игрока из событий social: маска claim и бан аккаунта (5c, ADR 0052).

Только новая таблица (expand): до первого события маска нулевая, как и раньше.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_sanctions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("mask", sa.Integer(), nullable=False),
        sa.Column("account_banned", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("next_expiry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "active",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_user_sanctions_sanctions_version_positive")
        ),
        sa.CheckConstraint(
            "mask BETWEEN 0 AND 65535", name=op.f("ck_user_sanctions_sanctions_mask_range")
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_sanctions")),
    )


def downgrade() -> None:
    op.drop_table("user_sanctions")
