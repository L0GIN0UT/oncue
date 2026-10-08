"""Схема БД collections. Новые таблицы объявлять на ``TABLES.metadata``."""

from __future__ import annotations

from gv_db import create_metadata

TABLES = create_metadata()
