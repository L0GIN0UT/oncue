"""Схема БД social. Новые таблицы объявлять на ``TABLES.metadata``."""

from __future__ import annotations

from gv_db import create_metadata

TABLES = create_metadata()

# Регистрация таблиц потоков на TABLES.metadata (по строке на модуль; порядок не важен).
from social_service.infrastructure.db import tables_friends  # noqa: E402, F401  # isort: skip
