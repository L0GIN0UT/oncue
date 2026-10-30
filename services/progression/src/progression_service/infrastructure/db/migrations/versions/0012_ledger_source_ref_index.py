"""Журналы: индекс ``(user_id, source_ref)`` для чтения наград за игровую сессию (expand-only).

``GET /v1/me/rewards?session_id=`` ищет начисления игрока по предмету (``source_ref`` = id
сессии). Без индекса поиск шёл бы просмотром всех проводок игрока по ``ix_*_user_at``.

Данные журналов и их триггеры не меняются; индексы строятся ``CONCURRENTLY`` вне транзакции
(не блокируют запись начислений; ``IF NOT EXISTS`` делает повтор после оборванной постройки
безопасным - невалидный индекс нужно удалить вручную). Права рантайм-роли ``progression_app``
(ADR 0060) индексов не касаются. Откат удаляет только эти два индекса.

Цепочка ревизий: ``0011`` -> ``0012`` (эта).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_xp_ledger_user_source_ref", "xp_ledger"),
    ("ix_currency_ledger_user_source_ref", "currency_ledger"),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table in _INDEXES:
            op.create_index(
                name,
                table,
                ["user_id", "source_ref"],
                postgresql_concurrently=True,
                if_not_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table in _INDEXES:
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
