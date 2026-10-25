"""Обработчики событий identity и game (R8).

* ``identity.UserRegistered`` / ``UserProfileBasicsChanged`` — ники (``users_proj``); если
  игрок в комнате — системная команда ``rename`` владельцу;
* ``identity.UserDeleted`` — ник стёрт, история обезличена, оценки удалены; игрок покидает
  комнату (системная команда ``remove``);
* ``game.ModeConfigPublished`` / ``ModeConfigArchived`` — текущие версии шаблонов
  (``mode_heads``): по ним ``POST /rooms`` фиксирует версию.

* ``social.UserSanctionsChanged`` — проекция санкций (``sanctions_proj``, ADR 0052); при
  ``ONLINE``/``RANKED`` игрок снимается с очереди, при ``ONLINE`` покидает комнату
  (системная команда ``evict`` владельцу, отзыв подписки Centrifugo с повтором);

* ``rating.LadderPublished`` / ``SeasonStarted`` / ``RatingChanged`` — проекция для подбора
  (``ladders_proj``, ``ratings_proj``, ADR 0038): рейтинг игрока — только по возрастанию
  ``version``, мягкий сброс сезона применяет сам match; синхронных вызовов rating нет;

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
повтор события — ``DUPLICATE`` по inbox, устаревшая ``version`` профиля — без изменений.
Команды владельцу — с ``op_id`` от ``event_id``: повторная доставка не применит их дважды
(окно ``op_id`` комнаты). Нарушение контракта payload — ``PermanentError`` (сразу в DLQ).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from prometheus_client import Counter
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gv_auth import SanctionEntry
from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from match_service.application.ports import RoomCommand
from match_service.domain.queue import FORMAT_ANSWER
from match_service.domain.sanctions import ONLINE_BAN, RANKED_BAN, SanctionsSnapshot
from match_service.infrastructure.container import MatchContainer
from match_service.infrastructure.db.ladders import SqlLadders, ladder_config
from match_service.infrastructure.db.repositories import SqlModeHeads, SqlUsers
from match_service.infrastructure.db.sanctions import SqlSanctions

log = structlog.get_logger(__name__)

UNKNOWN_FORMATS = Counter(
    "gv_match_ladder_unknown_formats_total", "LadderPublished с форматами вне FORMAT_ANSWER"
)

CONSUMED_TOPICS: tuple[str, ...] = (
    "identity.user.v1",
    "game.mode_config.v1",
    "rating.rating.v1",
    "social.social.v1",
)

USER_REGISTERED = "identity.UserRegistered"
USER_PROFILE_CHANGED = "identity.UserProfileBasicsChanged"
USER_DELETED = "identity.UserDeleted"
MODE_PUBLISHED = "game.ModeConfigPublished"
MODE_ARCHIVED = "game.ModeConfigArchived"
LADDER_PUBLISHED = "rating.LadderPublished"
SEASON_STARTED = "rating.SeasonStarted"
RATING_CHANGED = "rating.RatingChanged"
SANCTIONS_CHANGED = "social.UserSanctionsChanged"


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


LadderKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
"""Ключ ladder-а: попадает в ключи Redis и таблицы — только по схеме события."""


class _LadderPublished(_Payload):
    ladder: LadderKey
    version: int = Field(ge=1)
    domain: str
    formats: list[LadderKey] = Field(min_length=1)
    matchmaking: dict[str, Any]  # Any: блок схемы LadderPublished (проверяется потребителем)


class _SoftReset(_Payload):
    target: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)


class _SeasonStarted(_Payload):
    season_id: UUID
    ladder: LadderKey
    starts_at: datetime
    soft_reset: _SoftReset | None


class _RatingChanged(_Payload):
    user_id: UUID
    ladder: LadderKey
    season_id: UUID
    version: int = Field(ge=1)
    new: int
    at: datetime


class _ActiveSanction(_Payload):
    kind: str = Field(max_length=16)
    expires_at: AwareDatetime | None


class _SanctionsChanged(_Payload):
    user_id: UUID
    version: int = Field(ge=1)
    mask: int = Field(ge=0, le=0xFFFF)
    next_expiry_at: AwareDatetime | None
    active: list[_ActiveSanction]
    account_banned: bool = False


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def build_event_handlers(container: MatchContainer) -> EventHandlerRegistry:  # noqa: PLR0915 — обработчик на событие
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
        await SqlLadders.forget_user(session, p.user_id)
        if container.infra.redis is not None:
            await container.ranked_queue.remove(p.user_id)  # запись очереди идемпотентно
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

    async def on_ladder(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_LadderPublished, envelope.payload)
        try:
            ladder_config(p.matchmaking)
        except (KeyError, TypeError, ValueError) as exc:
            raise PermanentError("matchmaking не соответствует контракту") from exc
        unknown = sorted(set(p.formats) - set(FORMAT_ANSWER))
        if unknown:
            # Формат без шаблона-ответа очередью не обслуживается: enter вернёт 422.
            UNKNOWN_FORMATS.inc()
            log.warning("match.ladder_unknown_formats", ladder=p.ladder, formats=unknown)
        await SqlLadders.published(
            session,
            ladder=p.ladder,
            version=p.version,
            domain=p.domain,
            formats=p.formats,
            matchmaking=p.matchmaking,
            now=container.infra.clock.now(),
        )
        container.ladders.invalidate()

    async def on_season(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_SeasonStarted, envelope.payload)
        await SqlLadders.season_started(
            session,
            ladder=p.ladder,
            season_id=p.season_id,
            starts_at=p.starts_at,
            reset=p.soft_reset.model_dump() if p.soft_reset is not None else None,
            now=container.infra.clock.now(),
        )
        container.ladders.invalidate()

    async def on_rating(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_RatingChanged, envelope.payload)
        await SqlLadders.rating_changed(
            session,
            user_id=p.user_id,
            ladder=p.ladder,
            new=p.new,
            version=p.version,
            season_id=p.season_id,
            at=p.at,
            now=container.infra.clock.now(),
        )

    async def on_sanctions(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_SanctionsChanged, envelope.payload)
        now = container.infra.clock.now()
        snapshot = SanctionsSnapshot(
            user_id=p.user_id,
            version=p.version,
            entries=tuple(SanctionEntry(a.kind, a.expires_at) for a in p.active),
            mask=p.mask,
            next_expiry_at=p.next_expiry_at,
        )
        if not await SqlSanctions.apply(session, snapshot, now):
            return  # дубль или устаревшая версия: проекция не меняется, игрока не трогаем
        live = snapshot.mask_at(now)
        banned = bool(live & (ONLINE_BAN | RANKED_BAN)) or p.account_banned
        if banned and container.infra.redis is not None:
            await container.ranked_queue.remove(p.user_id)  # идемпотентно
        if p.account_banned:
            # Бан аккаунта (ADR 0059): ключ server API есть у match, не у identity. Повтор
            # безопасен: дубль/старая версия отсечены выше, disconnect идемпотентен.
            await container.subscriptions.disconnect(
                p.user_id, reconnect=False, reason="account_banned"
            )
            log.info("match.account_banned_disconnected")
        if live & ONLINE_BAN or p.account_banned:
            # Игрок покидает комнату/лобби через владельца (как кик), подписка отзывается.
            await notify_room(p.user_id, "evict", envelope, {})
            log.info("match.online_banned_evicted")

    registry.register(SANCTIONS_CHANGED, on_sanctions)
    registry.register(LADDER_PUBLISHED, on_ladder)
    registry.register(SEASON_STARTED, on_season)
    registry.register(RATING_CHANGED, on_rating)
    registry.register(USER_REGISTERED, on_user)
    registry.register(USER_PROFILE_CHANGED, on_user)
    registry.register(USER_DELETED, on_user_deleted)
    registry.register(MODE_PUBLISHED, on_mode_published)
    registry.register(MODE_ARCHIVED, on_mode_archived)
    return registry
