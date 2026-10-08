"""Метаданные схемы сервиса."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import MetaData, Table

from gv_messaging.inbox import inbox_table
from gv_messaging.outbox import outbox_table

# Предсказуемые имена ограничений: без них Alembic генерирует миграции, которые нельзя
# надёжно откатить или повторить на другой базе.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


@dataclass(frozen=True, slots=True)
class ServiceTables:
    """Метаданные сервиса и служебные таблицы."""

    metadata: MetaData
    outbox: Table
    inbox: Table


def create_metadata() -> ServiceTables:
    """Создать метаданные сервиса со служебными таблицами outbox и inbox."""
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    return ServiceTables(
        metadata=metadata, outbox=outbox_table(metadata), inbox=inbox_table(metadata)
    )
