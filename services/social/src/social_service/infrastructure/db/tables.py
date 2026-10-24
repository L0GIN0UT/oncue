"""Схема БД social. Новые таблицы объявлять на ``TABLES.metadata``."""

from __future__ import annotations

import importlib

from gv_db import create_metadata

TABLES = create_metadata()

# Таблицы областей объявляются в своих модулях на общем ``TABLES.metadata``; импорт нужен ради
# регистрации (модули импортируют ``TABLES`` отсюда, поэтому подключаем после его создания).
for _module in ("tables_friends", "tables_moderation"):
    importlib.import_module(f"{__package__}.{_module}")
