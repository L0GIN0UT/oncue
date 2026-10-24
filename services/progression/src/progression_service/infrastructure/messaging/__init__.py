"""Обработчики событий game, match и rating (начисление опыта и Версов, ADR 0042, 0043).

* ``game.GameFinished`` — соло-раунды (гости и дейлик пропускаются);
* ``game.DailyCompleted`` — награда и серия дейлика;
* ``match.MatchFinished`` — дуэль, party, lobby;
* ``rating.WeeklyTopFinalized`` / ``rating.SeasonStandingsFinalized`` — Версы по сеткам мест.

Обработчик работает в транзакции ``EventProcessor`` (отметка inbox + изменения атомарно);
use-cases получают UoW, привязанный к этой сессии (SAVEPOINT). Нарушение контракта payload —
``PermanentError`` (сразу в DLQ). Нет опубликованного конфига или он невалиден — обычная ошибка
(для невалидного — ERROR-лог ``progression.config_invalid_published``): сообщение повторится по
политике ретраев, а не потеряется молча.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, Literal
from uuid import UUID

import structlog
from prometheus_client import Gauge
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope, EventHandlerRegistry
from gv_messaging.ports import PermanentError
from gv_service import Infrastructure
from progression_service.application.accrual.common import AccrualContractError
from progression_service.application.accrual.daily import CompletedDaily
from progression_service.application.accrual.match import (
    FinishedMatch,
    MatchPlayerResult,
    RoundScores,
)
from progression_service.application.accrual.rating_rewards import (
    PlacedUser,
    SeasonStandings,
    WeeklyTop,
)
from progression_service.application.accrual.solo import SoloSession
from progression_service.application.quests.tracker import DailyDone, SoloFinished
from progression_service.domain.avatar import PURPOSE as AVATAR_PURPOSE
from progression_service.domain.avatar import Moderation, RequestStatus
from progression_service.domain.config import ConfigInvalidError
from progression_service.domain.experience import Finish, MatchFormat, Outcome
from progression_service.infrastructure.container import container_for
from progression_service.infrastructure.messaging.rewards import (
    COLLECTION_PUBLISHED_EVENT,
    COLLECTIONS_TOPIC,
    RewardHandlers,
)

log = structlog.get_logger(__name__)

UGC_IMAGE_TOPIC = "media_pipeline.ugc_image.v1"

CONSUMED_TOPICS: tuple[str, ...] = (
    "game.session.v1",
    "match.match.v1",
    "rating.rating.v1",
    COLLECTIONS_TOPIC,
    UGC_IMAGE_TOPIC,
)

GAME_FINISHED = "game.GameFinished"
DAILY_COMPLETED = "game.DailyCompleted"
MATCH_FINISHED = "match.MatchFinished"
WEEKLY_TOP_FINALIZED = "rating.WeeklyTopFinalized"
SEASON_STANDINGS_FINALIZED = "rating.SeasonStandingsFinalized"
ASSET_MODERATED = "media_pipeline.AssetModerated"

CONSUMER_LAG = Gauge(
    "gv_progression_consumer_lag",
    "Возраст последнего обработанного события (секунды): now - occurred_at (SLO ≤ 30 с, R10)",
    ["event_type"],
)

_VOID = "void"
_CORRECT = "exact"  # раунд угадан полностью: метрика correct_answers
Domain = Literal["music", "film"]


class _Payload(BaseModel):
    # Лишние поля контракта (ассеты, уровни, коды комнат…) начислению не нужны.
    model_config = ConfigDict(extra="ignore", frozen=True)


class _Round(_Payload):
    outcome: str
    points: int = Field(ge=0)


class _GameFinished(_Payload):
    session_id: UUID
    user_id: UUID | None
    is_guest: bool
    domain: Domain
    day: date | None
    finished_at: AwareDatetime
    rounds: list[_Round] = Field(min_length=1, max_length=200)


class _DailyCompleted(_Payload):
    session_id: UUID
    user_id: UUID
    domain: Domain
    day: date


class _Result(_Payload):
    user_id: UUID
    score: int = Field(ge=0)


class _MatchRound(_Payload):
    results: list[_Result] = Field(max_length=6)


class _Player(_Payload):
    user_id: UUID
    outcome: Literal["win", "loss", "draw"] | None
    place: int = Field(ge=1, le=6)
    finish: Literal["completed", "forfeit", "left", "tech_win", "tech_loss"]


class _MatchFinished(_Payload):
    match_id: UUID
    domain: Literal["music", "film"]
    format: Literal["duel", "party", "lobby"]
    aborted: bool
    finished_at: AwareDatetime
    players: list[_Player] = Field(min_length=2, max_length=6)
    rounds: list[_MatchRound] = Field(max_length=50)


class _Placed(_Payload):
    user_id: UUID
    place: int = Field(ge=1)


class _WeeklyTop(_Payload):
    ladder: str = Field(min_length=1, max_length=64)
    week_start: date
    top: list[_Placed] = Field(max_length=100)


class _SeasonStandings(_Payload):
    season_id: UUID
    ladder: str = Field(min_length=1, max_length=64)
    chunk: int = Field(ge=0)
    finished_at: AwareDatetime
    entries: list[_Placed] = Field(min_length=1, max_length=500)


class _AssetModerated(_Payload):
    asset_id: UUID
    owner_user_id: UUID
    purpose: str = Field(min_length=1, max_length=64)
    status: Literal["approved", "rejected"]
    reason_code: str | None = Field(max_length=64)
    public_key: str | None
    decided_at: AwareDatetime
    version: int = Field(ge=1)


def _moderation(p: _AssetModerated) -> Moderation:
    try:
        return Moderation(
            asset_id=p.asset_id,
            owner_user_id=p.owner_user_id,
            status=RequestStatus(p.status),
            reason_code=p.reason_code,
            public_key=p.public_key,
            decided_at=p.decided_at,
            version=p.version,
        )
    except ValueError as exc:  # противоречие полей контракта
        raise PermanentError(f"AssetModerated нарушает контракт: {exc}") from exc


def _parse[M: BaseModel](model: type[M], payload: dict[str, Any]) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


def _match(p: _MatchFinished) -> FinishedMatch:
    rounds: tuple[RoundScores, ...] = tuple(
        tuple((r.user_id, r.score) for r in rnd.results) for rnd in p.rounds
    )
    return FinishedMatch(
        match_id=p.match_id,
        format=MatchFormat(p.format),
        aborted=p.aborted,
        finished_at=p.finished_at,
        players=tuple(
            MatchPlayerResult(
                user_id=x.user_id,
                finish=Finish(x.finish),
                outcome=None if x.outcome is None else Outcome(x.outcome),
                place=x.place,
            )
            for x in p.players
        ),
        rounds=rounds,
    )


Handler = Callable[[EventEnvelope, AsyncSession], Awaitable[None]]


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:  # noqa: PLR0915 - реестр
    """Реестр обработчиков событий."""
    registry = EventHandlerRegistry()
    container = container_for(infra)
    rewards = RewardHandlers(container)

    def observe_lag(envelope: EventEnvelope) -> None:
        lag = (infra.clock.now() - envelope.occurred_at).total_seconds()
        CONSUMER_LAG.labels(envelope.event_type).set(max(0.0, lag))

    def handler(
        run: Callable[[EventEnvelope, AsyncSession], Awaitable[object]],
    ) -> Handler:
        """Лаг, перевод «нарушение контракта» в ``PermanentError``."""

        async def wrapped(envelope: EventEnvelope, session: AsyncSession) -> None:
            observe_lag(envelope)
            try:
                await run(envelope, session)
            except AccrualContractError as exc:
                # Битое событие повтором не лечится — сразу в DLQ.
                raise PermanentError(str(exc)) from exc
            except ConfigInvalidError as exc:
                # Битый опубликованный конфиг — состояние сервиса, не события: событие не теряем
                # (повтор по политике ретраев), владельцу — громкий сигнал.
                log.error(  # noqa: TRY400 — исключение пробрасываем, трассу пишет процессор
                    "progression.config_invalid_published",
                    event_type=envelope.event_type,
                    error=str(exc),
                )
                raise

        return wrapped

    async def on_game_finished(envelope: EventEnvelope, session: AsyncSession) -> object:
        p = _parse(_GameFinished, envelope.payload)
        result = await container.accrue_solo(session).execute(
            SoloSession(
                session_id=p.session_id,
                # Гость пропускается по любому из признаков: is_guest или user_id = null.
                user_id=None if p.is_guest else p.user_id,
                day=p.day,
                finished_at=p.finished_at,
                round_points=tuple(r.points for r in p.rounds if r.outcome != _VOID),
            )
        )
        played = [r for r in p.rounds if r.outcome != _VOID]
        await rewards.game_finished(
            SoloFinished(
                session_id=p.session_id,
                user_id=None if p.is_guest else p.user_id,
                domain=p.domain,
                finished_at=p.finished_at,
                rounds_played=len(played),
                correct_answers=sum(1 for r in played if r.outcome == _CORRECT),
            ),
            session,
        )
        return result

    async def on_daily_completed(envelope: EventEnvelope, session: AsyncSession) -> object:
        p = _parse(_DailyCompleted, envelope.payload)
        result = await container.accrue_daily(session).execute(
            CompletedDaily(
                session_id=p.session_id,
                user_id=p.user_id,
                domain=p.domain,
                day=p.day,
                occurred_at=envelope.occurred_at,
            )
        )
        # Серия дейлика уже обновлена P1 - метрика daily_streak берёт её из той же транзакции.
        await rewards.daily_completed(
            DailyDone(
                session_id=p.session_id,
                user_id=p.user_id,
                domain=p.domain,
                day=p.day,
                occurred_at=envelope.occurred_at,
            ),
            session,
        )
        return result

    async def on_match_finished(envelope: EventEnvelope, session: AsyncSession) -> object:
        parsed = _parse(_MatchFinished, envelope.payload)
        match = _match(parsed)
        result = await container.accrue_match(session).execute(match)
        await rewards.match_finished(match, parsed.domain, session)
        return result

    async def on_weekly_top(envelope: EventEnvelope, session: AsyncSession) -> object:
        p = _parse(_WeeklyTop, envelope.payload)
        return await container.accrue_weekly_top(session).execute(
            WeeklyTop(
                ladder=p.ladder,
                week_start=p.week_start,
                top=tuple(PlacedUser(e.user_id, e.place) for e in p.top),
                closed_at=envelope.occurred_at,
            )
        )

    async def on_season_standings(envelope: EventEnvelope, session: AsyncSession) -> object:
        p = _parse(_SeasonStandings, envelope.payload)
        standings = SeasonStandings(
            season_id=p.season_id,
            ladder=p.ladder,
            chunk=p.chunk,
            entries=tuple(PlacedUser(e.user_id, e.place) for e in p.entries),
            finished_at=p.finished_at,
        )
        result = await container.accrue_season(session).execute(standings)
        await rewards.season_standings(standings, session)
        return result

    async def on_collection_published(envelope: EventEnvelope, session: AsyncSession) -> object:
        await rewards.collection_published(envelope, session)
        return None

    async def on_asset_moderated(envelope: EventEnvelope, session: AsyncSession) -> object:
        p = _parse(_AssetModerated, envelope.payload)
        if p.purpose != AVATAR_PURPOSE:
            return None  # обложки сборников и прочее - не наша зона
        return await container.avatar_moderation(session).apply(_moderation(p))

    registry.register(GAME_FINISHED, handler(on_game_finished))
    registry.register(ASSET_MODERATED, handler(on_asset_moderated))
    registry.register(DAILY_COMPLETED, handler(on_daily_completed))
    registry.register(MATCH_FINISHED, handler(on_match_finished))
    registry.register(WEEKLY_TOP_FINALIZED, handler(on_weekly_top))
    registry.register(SEASON_STANDINGS_FINALIZED, handler(on_season_standings))
    registry.register(COLLECTION_PUBLISHED_EVENT, handler(on_collection_published))
    return registry


__all__ = ["CONSUMED_TOPICS", "CONSUMER_LAG", "build_event_handlers"]
