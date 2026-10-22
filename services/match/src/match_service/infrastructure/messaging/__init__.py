"""Обработчики событий identity и game (R8).

* ``identity.UserRegistered`` / ``UserProfileBasicsChanged`` — ники (``users_proj``); если
  игрок в комнате — системная команда ``rename`` владельцу;
* ``identity.UserDeleted`` — ник стёрт, история обезличена, оценки удалены; игрок покидает
  комнату (системная команда ``remove``);
* ``game.ModeConfigPublished`` / ``ModeConfigArchived`` — текущие версии шаблонов
  (``mode_heads``): по ним ``POST /rooms`` фиксирует версию.

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
повтор события — ``DUPLICATE`` по inbox, устаревшая ``version`` профиля — без изменений.
Команды владельцу — с ``op_id`` от ``event_id``: повторная доставка не применит их дважды
(окно ``op_id`` комнаты). Нарушение контракта payload — ``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from match_service.application.ports import RoomCommand
from match_service.infrastructure.container import MatchContainer
from match_service.infrastructure.db.repositories import SqlModeHeads, SqlUsers

log = structlog.get_logger(__name__)

CONSUMED_TOPICS: tuple[str, ...] = ("identity.user.v1", "game.mode_config.v1")

USER_REGISTERED = "identity.UserRegistered"
USER_PROFILE_CHANGED = "identity.UserProfileBasicsChanged"
USER_DELETED = "identity.UserDeleted"
MODE_PUBLISHED = "game.ModeConfigPublished"
MODE_ARCHIVED = "game.ModeConfigArchived"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class _UserPayload(_Payload):
    user_id: UUID
    nickname: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class _UserDeletedPayload(_Payload):
    user_id: UUID
    version: int = Field(ge=1)


class _ModePublished(_Payload):
    mode_key: str = Field(max_length=100)
    version: int = Field(ge=1)
    domain: str


class _ModeArchived(_Payload):
    mode_key: str = Field(max_length=100)
    version: int = Field(ge=1)


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def build_event_handlers(container: MatchContainer) -> EventHandlerRegistry:
    """Реестр обработчиков событий."""
    registry = EventHandlerRegistry()

    async def notify_room(user_id: UUID, kind: str, envelope: EventEnvelope, data: Any) -> None:
        code = await container.player_rooms.get(user_id)
        if code is None:
            return
        command = RoomCommand(
            op_id=f"sys-{kind}-{envelope.event_id.hex}", kind=kind, user_id=user_id, data=data
        )
        await container.bus.send(code, command)

    async def on_user(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserPayload, envelope.payload)
        if await SqlUsers.profile(session, p.user_id, p.nickname, p.version):
            await notify_room(p.user_id, "rename", envelope, {"nickname": p.nickname})

    async def on_user_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserDeletedPayload, envelope.payload)
        await SqlUsers.forget(session, p.user_id, p.version)
        await notify_room(p.user_id, "remove", envelope, {})
        log.info("match.user_forgotten")

    async def on_mode_published(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_ModePublished, envelope.payload)
        await SqlModeHeads.published(
            session, p.mode_key, p.version, p.domain, container.infra.clock.now()
        )

    async def on_mode_archived(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_ModeArchived, envelope.payload)
        await SqlModeHeads.archived(session, p.mode_key, p.version, container.infra.clock.now())

    registry.register(USER_REGISTERED, on_user)
    registry.register(USER_PROFILE_CHANGED, on_user)
    registry.register(USER_DELETED, on_user_deleted)
    registry.register(MODE_PUBLISHED, on_mode_published)
    registry.register(MODE_ARCHIVED, on_mode_archived)
    return registry
