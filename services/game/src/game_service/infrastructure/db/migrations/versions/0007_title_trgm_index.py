"""Этап 4b (expand-only): trigram-индекс названия проекции каталога.

``GET /admin/v1/projection/items`` ищет ``title ILIKE '%…%'`` — без trigram это
последовательное чтение всей ``catalog_items``. Индекс GIN ``gin_trgm_ops`` строится
``CREATE INDEX CONCURRENTLY`` вне транзакции (запись в проекцию не блокируется;
по размеру — одно чтение таблицы, ``ShareUpdateExclusiveLock``). Расширение ``pg_trgm``
создаёт суперпользователь при инициализации БД (сервисной роли нельзя); если его нет,
индекс пропускается (предупреждение в лог alembic), поиск остаётся рабочим, но медленным.
Повторный накат безопасен: ``IF NOT EXISTS``. Прерванный CONCURRENTLY оставляет
INVALID-индекс — удалить его (``DROP INDEX``) и накатить заново.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_catalog_items_title_trgm"
log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    has_trgm = (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
        .scalar()
    )
    if not has_trgm:
        log.warning("pg_trgm не установлен: индекс %s пропущен", INDEX)
        return
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX,
            "catalog_items",
            ["title"],
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX, table_name="catalog_items", postgresql_concurrently=True, if_exists=True
        )
