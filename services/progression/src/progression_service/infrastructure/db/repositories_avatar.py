"""Postgres-репозиторий своей аватарки (поток S3; ADR 0048)."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gv_core.ids import uuid7
from progression_service.domain.avatar import (
    SUPERSEDED,
    AvatarRequest,
    CurrentAvatar,
    Moderation,
    RequestStatus,
)
from progression_service.infrastructure.db.tables_avatar import avatar_requests, user_avatars


def _request(r: Any) -> AvatarRequest:
    return AvatarRequest(
        id=r.id,
        user_id=r.user_id,
        asset_id=r.asset_id,
        status=RequestStatus(r.status),
        submitted_at=r.submitted_at,
        decided_at=r.decided_at,
        reason_code=r.reason_code,
        version=r.version,
    )


def _current(r: Any) -> CurrentAvatar:
    return CurrentAvatar(
        user_id=r.user_id,
        asset_id=r.asset_id,
        public_key=r.public_key,
        version=r.version,
        approved_at=r.approved_at,
    )


class SqlAvatars:
    """Заявки и действующие аватарки."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_request(
        self, user_id: UUID, asset_id: UUID, submitted_at: datetime, request_id: UUID
    ) -> bool:
        """Вставить ``pending``; конфликт по ``asset_id`` - ``False`` (ничего не меняется)."""
        row = (
            await self._s.execute(
                pg_insert(avatar_requests)
                .values(
                    id=request_id,
                    user_id=user_id,
                    asset_id=asset_id,
                    status=RequestStatus.PENDING.value,
                    submitted_at=submitted_at,
                    version=0,
                )
                .on_conflict_do_nothing(index_elements=[avatar_requests.c.asset_id])
                .returning(avatar_requests.c.id)
            )
        ).first()
        return row is not None

    async def lock_user(self, user_id: UUID) -> None:
        """Рекомендательная блокировка транзакции: заявки одного игрока идут по очереди."""
        await self._s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"avatar:{user_id}"},
        )

    async def supersede_pending(self, user_id: UUID, keep_asset_id: UUID, now: datetime) -> int:
        """Прежние ``pending`` -> ``rejected/superseded``."""
        c = avatar_requests.c
        result = await self._s.execute(
            update(avatar_requests)
            .where(
                c.user_id == user_id,
                c.status == RequestStatus.PENDING.value,
                c.asset_id != keep_asset_id,
            )
            .values(status=RequestStatus.REJECTED.value, reason_code=SUPERSEDED, decided_at=now)
            .returning(c.id)
        )
        return len(result.all())

    async def latest_request(self, user_id: UUID) -> AvatarRequest | None:
        """Последняя заявка."""
        c = avatar_requests.c
        row = (
            await self._s.execute(
                select(avatar_requests)
                .where(c.user_id == user_id)
                .order_by(c.submitted_at.desc(), c.id.desc())
                .limit(1)
            )
        ).first()
        return _request(row) if row else None

    async def request_by_asset(self, asset_id: UUID) -> AvatarRequest | None:
        """Заявка по ассету."""
        row = (
            await self._s.execute(
                select(avatar_requests).where(avatar_requests.c.asset_id == asset_id)
            )
        ).first()
        return _request(row) if row else None

    async def apply_decision(self, decision: Moderation) -> bool:
        """``INSERT … ON CONFLICT DO UPDATE WHERE version < excluded.version``: проверка версии
        и запись - один оператор без гонки; строка блокируется до конца транзакции."""
        stmt = pg_insert(avatar_requests).values(
            id=uuid7(),
            user_id=decision.owner_user_id,
            asset_id=decision.asset_id,
            status=decision.status.value,
            submitted_at=decision.decided_at,  # заявки ещё нет: решение пришло раньше ответа
            decided_at=decision.decided_at,
            reason_code=decision.reason_code,
            version=decision.version,
        )
        row = (
            await self._s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[avatar_requests.c.asset_id],
                    set_={
                        "status": stmt.excluded.status,
                        "decided_at": stmt.excluded.decided_at,
                        "reason_code": stmt.excluded.reason_code,
                        "version": stmt.excluded.version,
                    },
                    where=avatar_requests.c.version < stmt.excluded.version,
                ).returning(avatar_requests.c.id)
            )
        ).first()
        return row is not None

    async def set_current(self, decision: Moderation) -> bool:
        """Сделать ассет действующим, если он одобрен позже текущего."""
        assert decision.public_key is not None  # noqa: S101 - инвариант Moderation
        stmt = pg_insert(user_avatars).values(
            user_id=decision.owner_user_id,
            asset_id=decision.asset_id,
            public_key=decision.public_key,
            version=decision.version,
            approved_at=decision.decided_at,
        )
        row = (
            await self._s.execute(
                stmt.on_conflict_do_update(
                    index_elements=[user_avatars.c.user_id],
                    set_={
                        "asset_id": stmt.excluded.asset_id,
                        "public_key": stmt.excluded.public_key,
                        "version": stmt.excluded.version,
                        "approved_at": stmt.excluded.approved_at,
                    },
                    where=or_(
                        user_avatars.c.approved_at < stmt.excluded.approved_at,
                        user_avatars.c.asset_id == stmt.excluded.asset_id,
                    ),
                ).returning(user_avatars.c.user_id)
            )
        ).first()
        return row is not None

    async def clear_current(self, user_id: UUID, asset_id: UUID) -> bool:
        """Снять аватар, если он действующий."""
        result = await self._s.execute(
            delete(user_avatars)
            .where(user_avatars.c.user_id == user_id, user_avatars.c.asset_id == asset_id)
            .returning(user_avatars.c.user_id)
        )
        return result.first() is not None

    async def current(self, user_id: UUID) -> CurrentAvatar | None:
        """Действующий аватар."""
        row = (
            await self._s.execute(select(user_avatars).where(user_avatars.c.user_id == user_id))
        ).first()
        return _current(row) if row else None

    async def currents(self, user_ids: Collection[UUID]) -> dict[UUID, CurrentAvatar]:
        """Действующие аватары нескольких игроков."""
        if not user_ids:
            return {}
        rows = await self._s.execute(
            select(user_avatars).where(user_avatars.c.user_id.in_(list(user_ids)))
        )
        return {r.user_id: _current(r) for r in rows}
