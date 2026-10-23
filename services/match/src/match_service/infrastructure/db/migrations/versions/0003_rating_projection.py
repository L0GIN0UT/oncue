"""Проекция rating для подбора: ladder-ы и рейтинги игроков (этап 4b, ADR 0038).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ladders_proj",
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("formats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("matchmaking", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ladders_proj_version_positive")),
        sa.PrimaryKeyConstraint("ladder", name=op.f("pk_ladders_proj")),
    )
    op.create_table(
        "seasons_proj",
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("soft_reset", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ladder", name=op.f("pk_seasons_proj")),
    )
    op.create_table(
        "ratings_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 0", name=op.f("ck_ratings_proj_version_not_negative")),
        sa.PrimaryKeyConstraint("user_id", "ladder", name=op.f("pk_ratings_proj")),
    )


def downgrade() -> None:
    op.drop_table("ratings_proj")
    op.drop_table("seasons_proj")
    op.drop_table("ladders_proj")
