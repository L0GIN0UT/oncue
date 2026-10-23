"""PostgreSQL: таблицы, репозитории, миграции.

Импорт пакета регистрирует в общих метаданных (``tables.TABLES``) таблицы админки из
``tables_admin``: любой импорт ``infrastructure.db.*`` сначала выполняет этот модуль.
"""

from progression_service.infrastructure.db import tables_admin

__all__ = ["tables_admin"]
