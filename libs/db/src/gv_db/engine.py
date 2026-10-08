"""Асинхронный движок SQLAlchemy и фабрика сессий."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(
    url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 5,
    statement_timeout_ms: int = 5000,
    application_name: str = "guessverse",
    behind_pgbouncer: bool = False,
) -> AsyncEngine:
    """Создать движок для ``postgresql+asyncpg://...``.

    Args:
        url: DSN базы сервиса.
        pool_size: Постоянные соединения в пуле процесса.
        max_overflow: Дополнительные соединения при пике.
        statement_timeout_ms: Серверный таймаут запроса — защита от зависших запросов.
        application_name: Видно в ``pg_stat_activity``; ставим имя сервиса.
        behind_pgbouncer: PgBouncer в режиме transaction pooling не поддерживает
            prepared statements asyncpg — отключаем их кэш.
    """
    connect_args: dict[str, object] = {
        "server_settings": {
            "statement_timeout": str(statement_timeout_ms),
            "application_name": application_name,
        },
        "timeout": 5,
    }
    if behind_pgbouncer:
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        # Значения параметров запросов не попадают в текст исключений и, значит, в логи
        # (там бывают хэши паролей, e-mail, токены).
        hide_parameters=True,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий. ``expire_on_commit=False`` — объекты читаемы после коммита."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
