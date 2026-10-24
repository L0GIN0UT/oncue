"""Токены отписки: хеш и срок на доставке рассылки.

Expand: две nullable-колонки в ``broadcast_deliveries`` (без значения по умолчанию - без
перезаписи таблицы), уникальный индекс по ``token_hash`` (только не NULL) и индекс по
``token_expires_at`` (для будущей очистки истёкших). Хранится только sha256 токена.

Доставки, созданные до этой миграции, токена не имеют: отписаться по их ссылке нельзя. Это
допустимо - HMAC-схема отозвана, рассылок в проде ещё не было. Откат удаляет колонки (выданные
токены перестают работать).

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


def upgrade() -> None:
    op.add_column("broadcast_deliveries", sa.Column("token_hash", sa.String(64), nullable=True))
    op.add_column(
        "broadcast_deliveries",
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_broadcast_deliveries_token_hash",
        "broadcast_deliveries",
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("token_hash IS NOT NULL"),
    )
    op.create_index(
        "ix_broadcast_deliveries_token_expires_at",
        "broadcast_deliveries",
        ["token_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_broadcast_deliveries_token_expires_at", table_name="broadcast_deliveries")
    op.drop_index("uq_broadcast_deliveries_token_hash", table_name="broadcast_deliveries")
    op.drop_column("broadcast_deliveries", "token_expires_at")
    op.drop_column("broadcast_deliveries", "token_hash")
