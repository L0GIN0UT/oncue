"""Postgres-хранилище версий конфига social и провайдер действующей версии (ADR 0053)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core.clock import Clock
from social_service.application.ports.config import ConfigUnavailableError
from social_service.application.ports.config_store import ConfigVersionRow
from social_service.domain.config import SocialConfig
from social_service.infrastructure.db.tables_friends import config_versions

log = structlog.get_logger(__name__)

# Произвольная константа ключа advisory-блокировки изменений версий конфига social.
_CONFIG_LOCK_KEY = 5_300_001


def _row(r: Any) -> ConfigVersionRow:  # Any: строка результата Core
    return ConfigVersionRow(
        version=r.version,
        status=r.status,
        body=dict(r.body),
        body_sha256=r.body_sha256,
        created_by=r.created_by,
        created_at=r.created_at,
        published_at=r.published_at,
    )


class SqlConfigStore:
    """Версии конфига в транзакции вызывающего."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_writes(self) -> None:
        await self._s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CONFIG_LOCK_KEY})

    async def list(self) -> list[ConfigVersionRow]:
        rows = await self._s.execute(
            select(config_versions).order_by(config_versions.c.version.desc())
        )
        return [_row(r) for r in rows]

    async def get(self, version: int) -> ConfigVersionRow | None:
        row = (
            await self._s.execute(
                select(config_versions).where(config_versions.c.version == version)
            )
        ).first()
        return None if row is None else _row(row)

    async def _by_status(self, status: str) -> ConfigVersionRow | None:
        row = (
            await self._s.execute(select(config_versions).where(config_versions.c.status == status))
        ).first()
        return None if row is None else _row(row)

    async def draft(self) -> ConfigVersionRow | None:
        return await self._by_status("draft")

    async def published(self) -> ConfigVersionRow | None:
        return await self._by_status("published")

    async def create_draft(
        self, body: dict[str, Any], sha256: str, *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        top = await self._s.scalar(select(func.max(config_versions.c.version)))
        row = (
            await self._s.execute(
                pg_insert(config_versions)
                .values(
                    version=(top or 0) + 1,
                    status="draft",
                    body=body,
                    body_sha256=sha256,
                    created_by=actor_id,
                    created_at=now,
                    published_at=None,
                )
                .returning(*config_versions.c)
            )
        ).one()
        return _row(row)

    async def replace_draft(
        self, version: int, body: dict[str, Any], sha256: str, *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        row = (
            await self._s.execute(
                update(config_versions)
                .where(config_versions.c.version == version, config_versions.c.status == "draft")
                .values(body=body, body_sha256=sha256, created_by=actor_id, created_at=now)
                .returning(*config_versions.c)
            )
        ).one()
        return _row(row)

    async def publish(self, version: int, *, now: datetime) -> ConfigVersionRow:
        # Сначала архив: частичный уникальный индекс допускает одну действующую версию.
        await self._s.execute(
            update(config_versions)
            .where(config_versions.c.status == "published")
            .values(status="archived")
        )
        row = (
            await self._s.execute(
                update(config_versions)
                .where(config_versions.c.version == version, config_versions.c.status == "draft")
                .values(status="published", published_at=now)
                .returning(*config_versions.c)
            )
        ).one()
        return _row(row)


class DbConfigProvider:
    """Действующий конфиг из БД с коротким кэшем в процессе (``ConfigProvider``).

    Кэш живёт ``ttl_s`` секунд: публикация новой версии действует без деплоя, но не на каждый
    запрос читает БД. Нет опубликованной версии или БД недоступна после истечения кэша -
    ``ConfigUnavailableError`` (fail-closed, 503 ``social.config_unavailable``).
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], clock: Clock, ttl_s: float
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._ttl_s = ttl_s
        self._cached: tuple[SocialConfig, float] | None = None
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        """Сбросить кэш (тесты, немедленное применение после публикации в этом процессе)."""
        self._cached = None

    async def current(self) -> SocialConfig:
        now = self._clock.now().timestamp()
        cached = self._cached
        if cached is not None and cached[1] > now:
            return cached[0]
        async with self._lock:
            cached = self._cached
            if cached is not None and cached[1] > self._clock.now().timestamp():
                return cached[0]
            config = await self._load()
            self._cached = (config, self._clock.now().timestamp() + self._ttl_s)
            return config

    async def _load(self) -> SocialConfig:
        try:
            async with self._sessions() as session:
                row = (
                    await session.execute(
                        select(config_versions.c.version, config_versions.c.body).where(
                            config_versions.c.status == "published"
                        )
                    )
                ).first()
        except (SQLAlchemyError, OSError) as exc:
            log.warning("social.config_db_unavailable", error_type=type(exc).__name__)
            raise ConfigUnavailableError("БД конфига недоступна") from exc
        if row is None:
            raise ConfigUnavailableError("нет опубликованной версии конфига")
        try:
            return SocialConfig.from_body(row.version, row.body)
        except (KeyError, TypeError) as exc:
            log.exception("social.config_published_malformed", version=row.version)
            raise ConfigUnavailableError("опубликованная версия конфига повреждена") from exc
