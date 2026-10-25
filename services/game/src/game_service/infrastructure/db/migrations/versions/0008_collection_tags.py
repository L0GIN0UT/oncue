"""Этап 6 (expand-only): теги сборника в проекции game (ADR 0067).

``collection_proj.tags`` - ключи тегов из необязательного ``tags`` событий
``collections.CollectionPublished|Updated``. Сессия по сборнику фиксирует их на старте и
отдаёт в ``GameFinished.collection_tags``. Старые строки получают ``[]`` (проекция
дополняется следующим событием сборника). Откат удаляет только колонку.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "collection_proj",
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("collection_proj", "tags")
