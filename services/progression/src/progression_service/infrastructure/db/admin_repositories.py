"""Postgres-репозитории admin-API progression (поток P3) и Unit of Work админки."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from sqlalchemy import func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gv_db.idempotency import SqlIdempotencyStore
from progression_service.application.admin.common import GRANT_REASON
from progression_service.application.admin.ports import AdminAction, ConfigVersionRow, LedgerRow
from progression_service.infrastructure.db.repositories import SqlLedger, SqlProgressionUnitOfWork
from progression_service.infrastructure.db.tables import config_versions, currency_ledger
from progression_service.infrastructure.db.tables_admin import admin_actions, idempotency_keys

# Произвольная константа ключа advisory-блокировки изменений версий конфига.
_CONFIG_LOCK_KEY = 5_100_001


def _version(r: Any) -> ConfigVersionRow:
    return ConfigVersionRow(
        version=r.version,
        status=r.status,
        body=dict(r.body),
        created_by=r.created_by,
        created_at=r.created_at,
        published_at=r.published_at,
    )


class SqlConfigAdmin:
    """Версии конфига для админки."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_writes(self) -> None:
        """Транзакционная advisory-блокировка: изменения версий идут по очереди."""
        await self._s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CONFIG_LOCK_KEY})

    async def list(self) -> list[ConfigVersionRow]:
        """Все версии, новые первыми."""
        rows = await self._s.execute(
            select(config_versions).order_by(config_versions.c.version.desc())
        )
        return [_version(r) for r in rows]

    async def get(self, version: int) -> ConfigVersionRow | None:
        """Версия по номеру."""
        row = (
            await self._s.execute(
                select(config_versions).where(config_versions.c.version == version)
            )
        ).first()
        return None if row is None else _version(row)

    async def draft(self) -> ConfigVersionRow | None:
        """Текущий черновик."""
        row = (
            await self._s.execute(
                select(config_versions).where(config_versions.c.status == "draft")
            )
        ).first()
        return None if row is None else _version(row)

    async def create_draft(
        self, body: dict[str, Any], *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        """Новый черновик со следующим номером."""
        top = await self._s.scalar(select(func.max(config_versions.c.version)))
        row = (
            await self._s.execute(
                pg_insert(config_versions)
                .values(
                    version=(top or 0) + 1,
                    status="draft",
                    body=body,
                    created_by=actor_id,
                    created_at=now,
                    published_at=None,
                )
                .returning(*config_versions.c)
            )
        ).one()
        return _version(row)

    async def replace_draft(
        self, version: int, body: dict[str, Any], *, actor_id: UUID, now: datetime
    ) -> ConfigVersionRow:
        """Заменить тело черновика (опубликованные версии не меняются)."""
        row = (
            await self._s.execute(
                update(config_versions)
                .where(config_versions.c.version == version, config_versions.c.status == "draft")
                .values(body=body, created_by=actor_id, created_at=now)
                .returning(*config_versions.c)
            )
        ).one()
        return _version(row)

    async def publish(self, version: int, *, now: datetime) -> ConfigVersionRow:
        """Архивировать действующую и опубликовать ``version`` (порядок важен для индекса)."""
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
        return _version(row)


class SqlAuditLog:
    """Аудит admin-действий."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        await self._s.execute(
            admin_actions.insert().values(
                id=action.id,
                actor_id=action.actor_id,
                action=action.action,
                target=action.target,
                reason=action.reason,
                details=action.details,
                at=action.at,
            )
        )


class SqlAdminLedger:
    """Чтение журнала Версов для админки."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def granted_between(self, user_id: UUID, start: datetime, end: datetime) -> int:
        """Сумма ручных выдач в ``[start, end)``."""
        total = await self._s.scalar(
            select(func.coalesce(func.sum(currency_ledger.c.amount), 0)).where(
                currency_ledger.c.user_id == user_id,
                currency_ledger.c.reason == GRANT_REASON,
                currency_ledger.c.direction == "credit",
                currency_ledger.c.at >= start,
                currency_ledger.c.at < end,
            )
        )
        return int(total or 0)

    async def page(
        self, user_id: UUID, *, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[LedgerRow]:
        """Проводки игрока, новые первыми, строго до курсора."""
        stmt = select(currency_ledger).where(currency_ledger.c.user_id == user_id)
        if before is not None:
            stmt = stmt.where(tuple_(currency_ledger.c.at, currency_ledger.c.id) < tuple_(*before))
        rows = await self._s.execute(
            stmt.order_by(currency_ledger.c.at.desc(), currency_ledger.c.id.desc()).limit(limit)
        )
        return [LedgerRow(id=r.id, entry=SqlLedger._entry(r)) for r in rows]


class SqlStats:
    """Счётчики заданий и бейджей.

    Таблиц заданий и бейджей в потоке P3 нет - нули. Поток P2 при слиянии заменяет эти методы
    запросами к своим таблицам.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def quest_completions(self) -> dict[str, int] | None:
        """Выполнений по ключу задания (нет таблиц P2: None - данных нет)."""
        return None

    async def badge_unlocks(self) -> dict[str, int] | None:
        """Выдано по ключу бейджа (нет таблиц P2: None - данных нет)."""
        return None


class SqlAdminUnitOfWork(SqlProgressionUnitOfWork):
    """Unit of Work админки: ядро progression плюс репозитории админки."""

    config_admin: SqlConfigAdmin
    audit: SqlAuditLog
    admin_ledger: SqlAdminLedger
    stats: SqlStats
    idempotency: SqlIdempotencyStore

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        session = self._session
        assert session is not None  # noqa: S101 — открыт в super().__aenter__
        self.config_admin = SqlConfigAdmin(session)
        self.audit = SqlAuditLog(session)
        self.admin_ledger = SqlAdminLedger(session)
        self.stats = SqlStats(session)
        self.idempotency = SqlIdempotencyStore(session, idempotency_keys, self._clock)
        return self
