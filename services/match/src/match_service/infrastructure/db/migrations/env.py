"""Окружение Alembic match. Логика общая — ``gv_db.migrations.run_env``."""

from match_service.infrastructure.db.tables import TABLES

from gv_db.migrations import run_env

run_env(TABLES.metadata)
