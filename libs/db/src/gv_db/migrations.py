"""Запуск миграций Alembic без ``alembic.ini``.

Миграции лежат внутри пакета сервиса (``<pkg>/infrastructure/db/migrations``), поэтому
попадают в образ вместе с кодом. Команда ``<service> migrate`` вызывает ``run_migrations``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, MetaData, pool
from sqlalchemy.ext.asyncio import create_async_engine


def alembic_config(script_location: Path, database_url: str) -> Config:
    """Собрать конфигурацию Alembic программно."""
    config = Config()
    config.set_main_option("script_location", str(script_location))
    # % экранируется, потому что Config использует ConfigParser-интерполяцию.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def run_migrations(script_location: Path, database_url: str, revision: str = "head") -> None:
    """Применить миграции до ``revision``. Синхронно: вызывается отдельной командой."""
    command.upgrade(alembic_config(script_location, database_url), revision)


def run_env(target_metadata: MetaData) -> None:
    """Тело ``env.py`` Alembic для сервиса: online-режим через async-движок.

    ``env.py`` сервиса сводится к двум строкам::

        from gv_db.migrations import run_env
        from <pkg>.infrastructure.db.tables import TABLES
        run_env(TABLES.metadata)
    """
    from alembic import context

    url = context.config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url не задан")

    if context.is_offline_mode():
        context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    def do_run(connection: Connection) -> None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    async def run_async() -> None:
        engine = create_async_engine(url, poolclass=pool.NullPool)
        async with engine.connect() as connection:
            await connection.run_sync(do_run)
        await engine.dispose()

    asyncio.run(run_async())
