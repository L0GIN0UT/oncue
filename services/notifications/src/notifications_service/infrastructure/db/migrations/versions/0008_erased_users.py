"""Tombstone удалённых игроков (``identity.UserDeleted``, ADR 0069).

Expand: новая пустая таблица; прежний код её не знает. Запись пачки массовой доставки
пропускает id из неё, поэтому уведомление не создаётся игроку, удалённому между чтением страницы
identity и записью пачки. Старые строки чистит периодическая задача по сроку из настроек.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "erased_users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_erased_users")),
    )
    op.create_index(op.f("ix_erased_users_erased_at"), "erased_users", ["erased_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_erased_users_erased_at"), table_name="erased_users")
    op.drop_table("erased_users")
