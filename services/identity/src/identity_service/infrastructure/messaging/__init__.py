"""Обработчики доменных событий других сервисов.

Чтобы подписаться на событие: добавить топик в ``CONSUMED_TOPICS`` и зарегистрировать
обработчик в ``build_event_handlers`` (обработчик вызывает use-case из application).

* ``social.UserSanctionsChanged`` - снимок санкций игрока (маска токена, бан аккаунта, ADR 0052).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from gv_service import Infrastructure
from identity_service.application.commands.auth import revoke_quietly
from identity_service.application.commands.user_sanctions import ApplyUserSanctions
from identity_service.infrastructure.db.repositories import SqlSessionRepository
from identity_service.infrastructure.db.repositories_sanctions import SqlUserSanctionRepository
from identity_service.infrastructure.redis_stores import RedisRevocationStore
from identity_service.settings import Settings

USER_SANCTIONS_CHANGED = "social.UserSanctionsChanged"
CONSUMED_TOPICS: tuple[str, ...] = ("social.social.v1",)


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков событий сервиса."""
    settings = infra.settings
    if not isinstance(settings, Settings):
        raise TypeError("identity ожидает identity_service.settings.Settings")
    revocation = (
        RedisRevocationStore(
            infra.redis,
            ttl_s=settings.jwt.access_ttl_s + settings.jwt.leeway_s,
            prefix=settings.redis_key_prefix,
        )
        if infra.redis is not None
        else None
    )

    async def user_sanctions_changed(envelope: EventEnvelope, session: AsyncSession) -> None:
        apply = ApplyUserSanctions(
            SqlUserSanctionRepository(session), SqlSessionRepository(session), infra.clock
        )
        try:
            result = await apply(envelope.payload)
        except (KeyError, ValueError, TypeError) as exc:
            # Битое событие повтором не лечится.
            raise PermanentError(
                f"{envelope.event_type}: payload не соответствует контракту"
            ) from exc
        if revocation is not None:
            # Отозванные сессии - в denylist, чтобы выданные access-токены перестали работать
            # сразу, а не через TTL; сбой Redis не откатывает отзыв в БД (refresh уже закрыт).
            await revoke_quietly(revocation, list(result.revoked_sessions))

    registry = EventHandlerRegistry()
    registry.register(USER_SANCTIONS_CHANGED, user_sanctions_changed)
    return registry
