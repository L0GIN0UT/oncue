"""Обработчики событий match и identity.

* ``match.MatchFinished`` — расчёт рейтинга (только рейтинговая дуэль, ADR 0037);
* ``identity.UserRegistered`` / ``UserProfileBasicsChanged`` / ``UserDeleted`` — ники лидербордов
  и скрытие удалённых аккаунтов.

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
use-cases получают UoW, привязанный к этой сессии (SAVEPOINT). Нарушение контракта payload или
правил рейтинга — ``PermanentError`` (сразу в DLQ: повтор бессмыслен).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from gv_service import Infrastructure
from rating_service.application.commands.match_rating import FinishedMatch, MatchPlayer
from rating_service.domain.elo import Outcome
from rating_service.domain.errors import LadderInvalidError, MatchContractError
from rating_service.infrastructure.container import container_for

CONSUMED_TOPICS: tuple[str, ...] = ("match.match.v1", "identity.user.v1")

MATCH_FINISHED = "match.MatchFinished"
USER_REGISTERED = "identity.UserRegistered"
USER_PROFILE_CHANGED = "identity.UserProfileBasicsChanged"
USER_DELETED = "identity.UserDeleted"


class _Payload(BaseModel):
    # Лишние поля контракта (раунды, коды комнат…) рейтингу не нужны.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Player(_Payload):
    user_id: UUID
    outcome: Literal["win", "loss", "draw"] | None
    finish: str = Field(max_length=16)


class _MatchFinished(_Payload):
    match_id: UUID
    format: str = Field(max_length=16)
    ranked: bool
    aborted: bool
    ladder: str | None = Field(max_length=64)
    # Допустимые форматы задаёт конфиг ladder-а; здесь только форма ключа.
    queue_format: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    finished_at: AwareDatetime
    players: list[_Player] = Field(min_length=2, max_length=6)


class _User(_Payload):
    user_id: UUID
    nickname: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)


class _UserDeleted(_Payload):
    user_id: UUID
    version: int = Field(ge=1)


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def _match(p: _MatchFinished) -> FinishedMatch:
    return FinishedMatch(
        match_id=p.match_id,
        kind=p.format,
        ranked=p.ranked,
        aborted=p.aborted,
        ladder=p.ladder,
        queue_format=p.queue_format,
        finished_at=p.finished_at,
        players=tuple(
            MatchPlayer(
                user_id=x.user_id,
                outcome=None if x.outcome is None else Outcome(x.outcome),
                finish=x.finish,
            )
            for x in p.players
        ),
    )


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков событий."""
    registry = EventHandlerRegistry()
    container = container_for(infra)

    async def on_match_finished(envelope: EventEnvelope, session: AsyncSession) -> None:
        match = _match(_parse(_MatchFinished, envelope.payload))
        try:
            await container.rate_match(session).execute(match)
        except (MatchContractError, LadderInvalidError) as exc:
            # Битый сохранённый конфиг и нарушение контракта события не лечатся повтором.
            raise PermanentError(str(exc)) from exc

    async def on_user(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_User, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.users.profile(uow, p.user_id, p.nickname, p.version)
            await uow.commit()

    async def on_user_deleted(envelope: EventEnvelope, session: AsyncSession) -> None:
        p = _parse(_UserDeleted, envelope.payload)
        async with container.uow_factory(session)() as uow:
            await container.users.forget(uow, p.user_id, p.version)
            await uow.commit()

    registry.register(MATCH_FINISHED, on_match_finished)
    registry.register(USER_REGISTERED, on_user)
    registry.register(USER_PROFILE_CHANGED, on_user)
    registry.register(USER_DELETED, on_user_deleted)
    return registry
