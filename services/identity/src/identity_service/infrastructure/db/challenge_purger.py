"""Очистка кодов из писем и доверенных устройств в Postgres (порт ``ChallengePurger``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from identity_service.infrastructure.db.tables import email_challenges, trusted_devices


class SqlChallengePurger:
    """Пачечный ``DELETE`` по индексам ``ix_email_challenges_expires_at`` и устройств.

    ``SKIP LOCKED`` не мешает коду, который прямо сейчас гасится или заменяется.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def purge(
        self,
        *,
        challenges_before: datetime,
        devices_revoked_before: datetime,
        now: datetime,
        limit: int,
    ) -> int:
        """Удалить до ``limit`` кодов и до ``limit`` устройств; вернуть общее число."""
        old_codes = (
            select(email_challenges.c.id)
            .where(email_challenges.c.expires_at <= challenges_before)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        old_devices = (
            select(trusted_devices.c.id)
            .where(
                or_(
                    trusted_devices.c.expires_at <= now,
                    trusted_devices.c.revoked_at <= devices_revoked_before,
                )
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        async with self._factory() as session, session.begin():
            codes = await session.execute(
                delete(email_challenges).where(email_challenges.c.id.in_(old_codes))
            )
            devices = await session.execute(
                delete(trusted_devices).where(trusted_devices.c.id.in_(old_devices))
            )
        return int(getattr(codes, "rowcount", 0) or 0) + int(getattr(devices, "rowcount", 0) or 0)
