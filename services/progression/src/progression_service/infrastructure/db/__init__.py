"""PostgreSQL: таблицы, репозитории, миграции."""

from progression_service.infrastructure.db import tables_rewards as _tables_rewards

# Таблицы P2 регистрируются в тех же метаданных: любой импорт db.* видит полную схему.
__all__ = ["_tables_rewards"]
