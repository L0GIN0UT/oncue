"""Машинные поля уведомления (``data``, 5c: room_invite -> room_code).

Expand: одна колонка jsonb NOT NULL со значением по умолчанию ``'{}'`` (в PostgreSQL 11+
добавление колонки с константным default не перезаписывает таблицу). Старые строки получают
пустой объект; код прежней версии колонку не читает и не пишет. Откат удаляет колонку.

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


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("notifications", "data")
