"""Этап 4b (expand-only): проекция ключей ladder-ов rating (ADR 0037).

Новая таблица ``ladders_proj`` из ``rating.LadderPublished``: game проверяет ею ссылку
``session.ladder`` рейтингового режима, не обращаясь к БД и API rating. Существующие
таблицы не меняются; откат удаляет только проекцию (она пересобирается из событий).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ladders_proj",
        sa.Column("ladder", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ladder", name=op.f("pk_ladders_proj")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ladders_proj_version_positive")),
        sa.CheckConstraint(
            "domain IN ('music', 'film')", name=op.f("ck_ladders_proj_domain_valid")
        ),
    )


def downgrade() -> None:
    op.drop_table("ladders_proj")
