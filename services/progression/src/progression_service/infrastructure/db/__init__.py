"""PostgreSQL: таблицы, репозитории, миграции.

Импорт пакета регистрирует в общих метаданных (``tables.TABLES``) таблицы наград (P2,
``tables_rewards``), админки (P3, ``tables_admin``), магазина (``tables_shop``) и своей аватарки
(``tables_avatar``): любой импорт ``infrastructure.db.*`` сначала выполняет этот модуль и видит
полную схему.
"""

from progression_service.infrastructure.db import (
    tables_admin,
    tables_avatar,
    tables_rewards,
    tables_shop,
)

__all__ = ["tables_admin", "tables_avatar", "tables_rewards", "tables_shop"]
