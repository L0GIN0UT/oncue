"""Обработчики событий identity и match для друзей и присутствия (поток F1; ADR 0050).

* ``identity.UserRegistered`` / ``identity.UserProfileBasicsChanged`` - ник в ``users_proj``
  (версия не новее применённой отбрасывается);
* ``match.MatchStarted`` / ``match.MatchFinished`` - проекция «в матче»;
* ``identity.UserDeleted`` - стереть дружбы, заявки, код, блокировки игрока.

Обработчик работает в транзакции ``EventProcessor`` (inbox и изменения атомарны). Нарушение
контракта payload - ``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from gv_service import Infrastructure
from social_service.infrastructure.container import container_for

IDENTITY_TOPIC = "identity.user.v1"
MATCH_TOPIC = "match.match.v1"
FRIENDS_TOPICS: tuple[str, ...] = (IDENTITY_TOPIC, MATCH_TOPIC)

USER_REGISTERED = "identity.UserRegistered"
PROFILE_CHANGED = "identity.UserProfileBasicsChanged"
USER_DELETED = "identity.UserDeleted"
MATCH_STARTED = "match.MatchStarted"
MATCH_FINISHED = "match.MatchFinished"

Handler = Callable[[EventEnvelope, AsyncSession], Awaitable[None]]


class _Payload(BaseModel):
    # Лишние поля контракта проекциям не нужны.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Nickname(_Payload):
    user_id: UUID
    nickname: Annotated[str, StringConstraints(min_length=3, max_length=24)]
    version: int = Field(ge=0)


class _Deleted(_Payload):
    user_id: UUID


class _Player(_Payload):
    user_id: UUID


class _Match(_Payload):
    match_id: UUID
    players: list[_Player] = Field(max_length=64)


def _parse[T: BaseModel](model: type[T], envelope: EventEnvelope) -> T:
    try:
        return model.model_validate(envelope.payload)
    except ValidationError as exc:
        raise PermanentError(f"{envelope.event_type}: payload не соответствует контракту") from exc


@dataclass(frozen=True, slots=True)
class SharedHandlers:
    """Обработчики друзей для событий, которые слушает и moderation (F2).

    ``EventHandlerRegistry`` не принимает второй обработчик на тот же тип события, поэтому
    при слиянии с F2 вызывайте ``register_friends_handlers(..., include_shared=False)`` и
    зовите эти функции из составных обработчиков ``identity.UserDeleted`` и
    ``match.MatchFinished`` (обе работают в той же транзакции процессора).
    """

    user_deleted: Handler
    match_finished: Handler


def register_friends_handlers(
    registry: EventHandlerRegistry, infra: Infrastructure, *, include_shared: bool = True
) -> SharedHandlers:
    """Зарегистрировать обработчики друзей (вызывается из ``build_event_handlers``).

    Args:
        registry: Реестр сервиса.
        infra: Ресурсы процесса.
        include_shared: ``False`` - не регистрировать ``UserDeleted`` и ``MatchFinished``
            (их регистрирует moderation и вызывает ``SharedHandlers``).
    """
    container = container_for(infra)

    async def on_nickname(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_Nickname, envelope)
        await container.projections(session).apply_nickname(p.user_id, p.nickname, p.version)

    async def on_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_Deleted, envelope)
        await container.projections(session).user_deleted(p.user_id)

    async def on_match_started(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_Match, envelope)
        await container.projections(session).match_started(
            p.match_id, [player.user_id for player in p.players]
        )

    async def on_match_finished(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_Match, envelope)
        await container.projections(session).match_finished(p.match_id)

    registry.register(USER_REGISTERED, on_nickname)
    registry.register(PROFILE_CHANGED, on_nickname)
    registry.register(MATCH_STARTED, on_match_started)
    if include_shared:
        registry.register(USER_DELETED, on_deleted)
        registry.register(MATCH_FINISHED, on_match_finished)
    return SharedHandlers(user_deleted=on_deleted, match_finished=on_match_finished)
