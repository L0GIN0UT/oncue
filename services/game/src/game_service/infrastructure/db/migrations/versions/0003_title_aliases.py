"""Алиасы-названия единиц в проекции каталога (expand-only).

Колонка с пустым значением по умолчанию: строки, применённые до миграции, получат
значения со следующим событием catalog; до тех пор ``accept_same_title`` сравнивает
только основное название.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-11
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
    op.add_column(
        "catalog_items",
        sa.Column(
            "title_aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("catalog_items", "title_aliases")
