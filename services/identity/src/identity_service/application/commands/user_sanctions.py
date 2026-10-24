"""Применение события ``social.UserSanctionsChanged`` (ADR 0052).

Порядок решает ``version``: событие с версией не больше применённой отбрасывается (дубль,
переупорядочивание). Бан аккаунта (``ACCOUNT``) отзывает все сессии и refresh-токены игрока
в той же транзакции; ``sid`` для denylist возвращаются вызывающему - он пишет их после.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from gv_auth import parse_entries
from gv_core.clock import Clock
from identity_service.application.ports import SessionRepository
from identity_service.application.ports.sanctions import UserSanctionRepository
from identity_service.domain.user_sanctions import UserSanctions

log = structlog.get_logger(__name__)

REVOKE_REASON = "account_banned"
MASK_MAX = 0xFFFF
"""Верхняя граница маски (схема события и CHECK таблицы)."""


@dataclass(frozen=True, slots=True)
class SanctionsApplied:
    """Итог применения события."""

    applied: bool
    """``False`` - событие устарело (версия не новее применённой)."""
    revoked_sessions: tuple[UUID, ...] = ()
    """Сессии, отозванные баном аккаунта (для denylist ``sid``)."""


def _snapshot(payload: Mapping[str, Any], now: datetime) -> UserSanctions:
    """Разобрать payload.

    Raises:
        KeyError, ValueError, TypeError: payload не соответствует контракту.
    """
    version = payload["version"]
    mask = payload["mask"]
    banned = payload["account_banned"]
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        or isinstance(mask, bool)
        or not isinstance(mask, int)
        or not 0 <= mask <= MASK_MAX
        or not isinstance(banned, bool)
    ):
        raise ValueError("недопустимые version/mask/account_banned")
    raw_expiry = payload["next_expiry_at"]
    expiry = datetime.fromisoformat(raw_expiry) if raw_expiry is not None else None
    if expiry is not None and expiry.tzinfo is None:
        raise ValueError("next_expiry_at без часового пояса")
    return UserSanctions(
        user_id=UUID(payload["user_id"]),
        version=version,
        entries=parse_entries(payload["active"]),
        mask=mask,
        account_banned=banned,
        next_expiry_at=expiry,
        updated_at=now,
    )


class ApplyUserSanctions:
    """Сохранить снимок санкций и, при бане аккаунта, отозвать сессии."""

    def __init__(
        self, sanctions: UserSanctionRepository, sessions: SessionRepository, clock: Clock
    ) -> None:
        self._sanctions, self._sessions, self._clock = sanctions, sessions, clock

    async def __call__(self, payload: Mapping[str, Any]) -> SanctionsApplied:
        """Применить payload события (в транзакции consumer).

        Raises:
            KeyError, ValueError, TypeError: payload не соответствует контракту (повтор не
                поможет - вызывающий отправляет событие в DLQ).
        """
        now = self._clock.now()
        snapshot = _snapshot(payload, now)
        if not await self._sanctions.apply(snapshot):
            log.info("sanctions.event_stale", version=snapshot.version)
            return SanctionsApplied(applied=False)
        if not snapshot.banned_at(now):
            return SanctionsApplied(applied=True)
        revoked: list[UUID] = []
        for session in await self._sessions.active_for_user(snapshot.user_id):
            if session.revoke(now, REVOKE_REASON):
                await self._sessions.save(session)
                revoked.append(session.id)
        log.info("sanctions.account_banned", sessions_revoked=len(revoked))
        return SanctionsApplied(applied=True, revoked_sessions=tuple(revoked))
