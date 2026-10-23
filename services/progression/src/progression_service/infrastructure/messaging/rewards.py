"""Обработчики событий заданий и бейджей (поток P2): метрики из game, match, collections, rating.

Для событий, которые обрабатывает и P1 (``GameFinished``, ``DailyCompleted``, ``MatchFinished``,
``SeasonStandingsFinalized``), payload разбирает один раз обработчик P1 (``messaging/__init__.py``),
а сюда приходят уже проверенные данные: P2 не может быть строже P1 и дать ``PermanentError`` там,
где начисление P1 прошло. Вызываются в той же транзакции ``EventProcessor`` после начисления P1 -
ошибка P2 откатывает и начисление, событие уходит в повтор. Самостоятельный разбор - только
у ``collections.CollectionPublished``.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gv_messaging import EventEnvelope
from gv_messaging.ports import PermanentError
from progression_service.application.accrual.match import FinishedMatch
from progression_service.application.accrual.rating_rewards import SeasonStandings
from progression_service.application.quests.tracker import (
    CollectionDone,
    DailyDone,
    ProgressTracker,
    SoloFinished,
)
from progression_service.infrastructure.container import ProgressionContainer

COLLECTION_PUBLISHED_EVENT = "collections.CollectionPublished"
COLLECTIONS_TOPIC = "collections.collection.v1"


class _CollectionPublished(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    collection_id: UUID
    author_id: UUID | None
    domain: Literal["music", "film"]
    status: Literal["published", "hidden"]
    published_at: AwareDatetime


def _parse_collection(payload: dict[str, Any]) -> _CollectionPublished:
    try:
        return _CollectionPublished.model_validate(payload)
    except ValidationError as exc:
        raise PermanentError(
            f"payload не соответствует контракту: {exc.error_count()} ошибок"
        ) from exc


class RewardHandlers:
    """Метрики заданий и бейджей по событиям; транзакция - сессия consumer."""

    def __init__(self, container: ProgressionContainer) -> None:
        self._container = container

    def _tracker(self, session: AsyncSession) -> ProgressTracker:
        return self._container.tracker(session)

    async def game_finished(self, game: SoloFinished, session: AsyncSession) -> None:
        """Раунды и угаданные соло-сессии."""
        await self._tracker(session).on_game_finished(game)

    async def daily_completed(self, daily: DailyDone, session: AsyncSession) -> None:
        """Дейлик пройден (после серии P1)."""
        await self._tracker(session).on_daily_completed(daily)

    async def match_finished(
        self, match: FinishedMatch, domain: str, session: AsyncSession
    ) -> None:
        """Онлайн-матч и победы игроков."""
        await self._tracker(session).on_match_finished(match, domain)

    async def season_standings(self, standings: SeasonStandings, session: AsyncSession) -> None:
        """Звание сезона игрокам пачки."""
        await self._tracker(session).on_season_standings(standings)

    async def collection_published(self, envelope: EventEnvelope, session: AsyncSession) -> None:
        """Сборник опубликован автором (скрытые модератором не считаются)."""
        p = _parse_collection(envelope.payload)
        if p.status != "published":
            return
        await self._tracker(session).on_collection_published(
            CollectionDone(
                collection_id=p.collection_id,
                author_id=p.author_id,
                domain=p.domain,
                published_at=p.published_at,
            )
        )
