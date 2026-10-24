"""Обработчики событий для комментариев, жалоб и шкал порядочности (5c, поток F2).

Слушаем ``collections.*`` (проекция сборников), ``match.MatchFinished`` / ``PlayerRated``
(участники, счётчики, жалобы из оценок) и ``identity.UserDeleted`` (обезличивание). Обработчик
работает в транзакции ``EventProcessor``: отметка inbox и запись атомарны. Событие, не
соответствующее контракту, - ``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from gv_service import Infrastructure
from social_service.application.projections import ProjectionService
from social_service.infrastructure.discussion_container import container_for

MODERATION_TOPICS: tuple[str, ...] = (
    "collections.collection.v1",
    "match.match.v1",
    "identity.user.v1",
)

type _Apply = Callable[[ProjectionService], Callable[[Mapping[str, Any]], Awaitable[None]]]

_HANDLERS: dict[str, _Apply] = {
    "collections.CollectionPublished": lambda s: s.collection_snapshot,
    "collections.CollectionUpdated": lambda s: s.collection_snapshot,
    "collections.CollectionDeleted": lambda s: s.collection_deleted,
    "match.MatchFinished": lambda s: s.match_finished,
    "match.PlayerRated": lambda s: s.player_rated,
    "identity.UserDeleted": lambda s: s.user_deleted,
}


def register_moderation_handlers(registry: EventHandlerRegistry, infra: Infrastructure) -> None:
    """Добавить в реестр обработчики проекций и жалоб."""
    container = container_for(infra)

    def make(select: _Apply) -> Callable[[EventEnvelope, AsyncSession], Awaitable[None]]:
        async def handle(envelope: EventEnvelope, session: AsyncSession) -> None:
            apply = select(container.projections(session))
            try:
                await apply(envelope.payload)
            except (KeyError, ValueError, TypeError) as exc:
                # Битое событие повтором не лечится.
                raise PermanentError(
                    f"{envelope.event_type}: payload не соответствует контракту"
                ) from exc

        return handle

    for event_type, select in _HANDLERS.items():
        registry.register(event_type, make(select))
