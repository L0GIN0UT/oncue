"""Выключатели типов уведомлений (admin, ADR 0066).

Expand: новая пустая таблица, существующие не меняются; прежний код её не знает и не ломается.
Нет строки - тип включён (значение по умолчанию). Блокировок существующих таблиц нет:
``CREATE TABLE`` берёт короткий lock только на каталог. Откат удаляет таблицу (решения админов
теряются - типы снова включены, что безопасно).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_type_flags",
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("type", name=op.f("pk_notification_type_flags")),
    )


def downgrade() -> None:
    op.drop_table("notification_type_flags")
