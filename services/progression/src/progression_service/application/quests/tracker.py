"""Метрики игрока из событий: прогресс заданий, награды, бейджи (ADR 0042, 0044; R1, R7, R8).

Одно событие - один предмет (сессия, матч, сборник…). Предмет отмечается в ``metric_marks``, поэтому
повтор события с новым ``event_id`` метрики не двоит. Период задания берётся по времени события
(``finished_at``), а не обработки (R1); конфиг - на тот же момент. Награда выдаётся проводками
ledger P1 (``Poster``) в той же транзакции, что и отметка выполнения.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import config_at
from progression_service.application.accrual.match import (
    FinishedMatch,
    MatchPlayerResult,
    rounds_stats,
)
from progression_service.application.accrual.posting import Poster, local_day
from progression_service.application.accrual.rating_rewards import SeasonStandings, WeeklyTop
from progression_service.application.badges import unlock_badges
from progression_service.application.events.progress import apply_metric_updates
from progression_service.application.ports import (
    ProgressionUnitOfWork,
    PublishedConfig,
    UnitOfWorkFactory,
)
from progression_service.application.ports.rewards import AssignedQuest
from progression_service.application.quests import ensure_assigned
from progression_service.domain import badges, metrics, quests
from progression_service.domain.events import QuestCompleted, RewardPayload
from progression_service.domain.experience import NO_REWARD_FINISHES, MatchFormat, OnlineResult
from progression_service.domain.metrics import MetricUpdate, merge

log = structlog.get_logger(__name__)

QUEST_REASON = "quest"
UpdateBuilder = Callable[
    [PublishedConfig, ProgressionUnitOfWork], Awaitable[Sequence[MetricUpdate]]
]
"""Строит изменения метрик по конфигу события (и, при нужде, по данным игрока в транзакции)."""


@dataclass(frozen=True, slots=True)
class SoloFinished:
    """Данные ``GameFinished`` для метрик: раунды и угаданные (гость - ``user_id = None``)."""

    session_id: UUID
    user_id: UUID | None
    domain: str
    finished_at: datetime
    rounds_played: int
    correct_answers: int
    collection_id: UUID | None = None
    collection_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DailyDone:
    """Данные ``DailyCompleted`` для метрик."""

    session_id: UUID
    user_id: UUID
    domain: str
    day: date
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CollectionDone:
    """Данные ``CollectionPublished`` для метрик (кураторский сборник - ``author_id = None``)."""

    collection_id: UUID
    author_id: UUID | None
    domain: str
    published_at: datetime


def _fixed(updates: list[MetricUpdate]) -> UpdateBuilder:
    """Построитель с заранее известными изменениями."""

    async def build(_: PublishedConfig, __: ProgressionUnitOfWork) -> list[MetricUpdate]:
        return updates

    return build


def _match_updates(match: FinishedMatch, player: MatchPlayerResult, domain: str) -> UpdateBuilder:
    """Метрики игрока онлайн-матча: сам матч и победа (по порогу раундов из конфига)."""
    played, won = rounds_stats(match.rounds, player.user_id)

    async def build(published: PublishedConfig, _: ProgressionUnitOfWork) -> list[MetricUpdate]:
        if player.finish in NO_REWARD_FINISHES:
            return []
        result = OnlineResult(
            format=match.format,
            finish=player.finish,
            outcome=player.outcome,
            place=player.place,
            rounds_total=len(match.rounds),
            rounds_played=played,
            rounds_won=won,
        )
        updates = [MetricUpdate(metrics.ONLINE_MATCHES, 1, domain)]
        # Победа - как «первая победа дня» (R4, R5): матч завершён и сыгран достаточно раундов.
        ratio = published.config.min_rounds_ratio
        if result.result_bonuses_allowed(ratio) and result.is_win():
            updates.append(MetricUpdate(metrics.WINS, 1, domain))
            if match.format is MatchFormat.DUEL:
                updates.append(MetricUpdate(metrics.DUEL_WIN, 1, domain))
        return updates

    return build


class ProgressTracker:
    """Применяет события к метрикам, заданиям и бейджам."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    # ------------------------------------------------------------------ источники событий
    async def on_game_finished(self, game: SoloFinished) -> bool:
        """Раунды и угаданные соло-сессии (гость пропускается, R7)."""
        if game.user_id is None or game.rounds_played < 1:
            return False  # гость или сессия из одних void-раундов: метрики не двигаются
        updates = [
            MetricUpdate(metrics.ROUNDS_PLAYED, game.rounds_played, game.domain),
            MetricUpdate(metrics.CORRECT_ANSWERS, game.correct_answers, game.domain),
        ]
        if game.collection_id is not None:  # теги - необязательное поле события (ADR 0067)
            updates.append(
                MetricUpdate(
                    metrics.COLLECTION_PLAYED,
                    1,
                    game.domain,
                    collection_id=str(game.collection_id),
                    collection_tags=game.collection_tags,
                )
            )
        return await self.record(
            f"game:{game.session_id}", game.user_id, game.finished_at, _fixed(updates)
        )

    async def on_daily_completed(self, daily: DailyDone) -> bool:
        """Дейлик пройден; серия домена (из серий P1) - уровень метрики ``daily_streak``."""

        async def build(_: PublishedConfig, uow: ProgressionUnitOfWork) -> list[MetricUpdate]:
            streak = (await uow.streaks.all(daily.user_id)).get(daily.domain)
            updates = [MetricUpdate(metrics.DAILY_COMPLETED, 1, daily.domain)]
            if streak is not None:
                updates.append(MetricUpdate(metrics.DAILY_STREAK, streak.current, daily.domain))
            return updates

        return await self.record(
            f"daily:{daily.session_id}", daily.user_id, daily.occurred_at, build
        )

    async def on_match_finished(self, match: FinishedMatch, domain: str) -> int:
        """Онлайн-матч и победы игроков (прерванный матч пропускается); число засчитанных."""
        if match.aborted:
            return 0
        counted = 0
        for player in sorted(match.players, key=lambda p: p.user_id):
            counted += await self.record(
                f"match:{match.match_id}:{player.user_id}",
                player.user_id,
                match.finished_at,
                _match_updates(match, player, domain),
            )
        return counted

    async def on_collection_published(self, done: CollectionDone) -> bool:
        """Сборник опубликован: считается один раз на сборник (новые версии не в счёт)."""
        if done.author_id is None:
            return False
        updates = [MetricUpdate(metrics.COLLECTION_PUBLISHED, 1, done.domain)]
        return await self.record(
            f"collection:{done.collection_id}", done.author_id, done.published_at, _fixed(updates)
        )

    async def on_weekly_top(self, top: WeeklyTop) -> int:
        """Место игрока в топе недели (для триггера ``weekly_top_place``); число засчитанных."""
        counted = 0
        for entry in sorted(top.top, key=lambda e: e.user_id):
            update = MetricUpdate(metrics.WEEKLY_TOP_PLACE, 1, place=entry.place)
            counted += await self.record(
                f"weekly:{top.ladder}:{top.week_start.isoformat()}:{entry.user_id}",
                entry.user_id,
                top.closed_at,
                _fixed([update]),
            )
        return counted

    async def on_season_standings(self, standings: SeasonStandings) -> int:
        """Звание на конец сезона каждому игроку пачки; вернуть число засчитанных."""
        async with self._uow() as uow:
            bands = (await config_at(uow, standings.finished_at)).config.verses.season
        counted = 0
        for entry in sorted(standings.entries, key=lambda e: e.user_id):
            tier = badges.season_tier(bands, entry.place)
            if tier < 1:
                continue
            counted += await self.record(
                f"season:{standings.season_id}:{standings.ladder}:{entry.user_id}",
                entry.user_id,
                standings.finished_at,
                _fixed([MetricUpdate(metrics.SEASON_TIER, tier)]),
            )
        return counted

    # ------------------------------------------------------------------ ядро
    async def record(
        self, subject_key: str, user_id: UUID, at: datetime, build: UpdateBuilder
    ) -> bool:
        """Применить изменения метрик одного предмета события; ``False`` - предмет уже учтён.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига (повторимо).
        """
        async with self._uow() as uow:
            if not await self.apply(uow, subject_key, user_id, at, build):
                return False
            await uow.commit()
        return True

    async def apply(
        self,
        uow: ProgressionUnitOfWork,
        subject_key: str,
        user_id: UUID,
        at: datetime,
        build: UpdateBuilder,
    ) -> bool:
        """То же, что ``record``, в транзакции вызывающего (колесо): коммитит вызывающий."""
        published = await config_at(uow, at)
        now = self._clock.now()
        poster = Poster(uow, published, now)
        await poster.lock_user(user_id)
        if not await uow.metrics.mark(subject_key, user_id, now):
            return False
        updates = await build(published, uow)
        values = await uow.metrics.values(user_id)
        for update in updates:
            values[update.metric] = merge(values.get(update.metric, 0), update)
            await uow.metrics.save(user_id, update.metric, values[update.metric], now)
        day = local_day(at, published)
        for assigned in await ensure_assigned(uow, published, user_id, day, now):
            await self._advance(uow, poster, assigned, updates, user_id, day, now)
        await unlock_badges(uow, published, user_id, values, now)
        # Находки сезонного события (ADR 0055): время события, в той же транзакции.
        await apply_metric_updates(uow, published, poster, user_id, at, updates, now)
        log.info("progression.metrics_recorded", subject=subject_key, updates=len(updates))
        return True

    async def _advance(  # noqa: PLR0917 - контекст транзакции передаётся явно
        self,
        uow: ProgressionUnitOfWork,
        poster: Poster,
        assigned: AssignedQuest,
        updates: Sequence[MetricUpdate],
        user_id: UUID,
        day: date,
        now: datetime,
    ) -> None:
        snap = assigned.snapshot
        if assigned.completed_at is not None:
            return
        progress = assigned.progress
        for update in updates:
            if snap.accepts(update):
                progress = snap.advance(progress, update)
        if progress == assigned.progress:
            return
        done = progress >= snap.target
        await uow.quests.save_progress(
            user_id, snap.key, snap.period_start, progress, now if done else None, now
        )
        if not done:
            return
        op = quests.op_id(user_id, snap.key, snap.period_start)
        ref = f"{snap.key}:{snap.period_start.isoformat()}"
        await poster.xp(
            user_id,
            op_id=op,
            base_amount=snap.reward.xp,
            reason=QUEST_REASON,
            source_ref=ref,
        )
        await poster.verses(
            user_id, op_id=op, amount=snap.reward.verses, reason=QUEST_REASON, source_ref=ref
        )
        # Разовое задание: период - дата выполнения (контракт события); ключ награды - дата
        # назначения, она одна на игрока и задание.
        event_start = day if snap.period == quests.ONE_TIME else snap.period_start
        uow.publish(
            QuestCompleted(
                aggregate_id=str(user_id),
                user_id=user_id,
                quest_key=snap.key,
                period=snap.period,
                period_start=event_start.isoformat(),
                reward=RewardPayload(xp=snap.reward.xp, verses=snap.reward.verses),
                config_version=snap.config_version,
                at=now,
            )
        )
