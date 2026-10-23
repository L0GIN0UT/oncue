"""Расчёт рейтинга по ``match.MatchFinished`` (ADR 0037): только рейтинговая дуэль."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

import structlog
from prometheus_client import Counter

from gv_core.clock import Clock
from rating_service.application.commands.leaderboards import Leaderboards
from rating_service.application.common import require_pair
from rating_service.application.ports import UnitOfWorkFactory
from rating_service.domain.elo import EloCalculator, Outcome
from rating_service.domain.errors import MatchContractError, NoActiveSeasonError
from rating_service.domain.rating import Rating
from rating_service.domain.weekly import week_start

log = structlog.get_logger(__name__)

WEEKLY_LATE_MATCHES = Counter(
    "gv_rating_weekly_late_matches_total",
    "Матчи, дошедшие после закрытия своей недели топа (засчитаны в текущую)",
    ["ladder"],
)

# Классификация ``MatchFinished.players[].finish`` (перечисление контракта): кто покинул матч.
# Размер штрафа — ``elo.leaver_penalty`` конфига; здесь только «кто считается вышедшим».
LEAVER_FINISHES = frozenset({"left", "forfeit", "tech_loss"})
DUEL_KIND = "duel"


@dataclass(frozen=True, slots=True)
class MatchPlayer:
    """Игрок завершённого матча."""

    user_id: UUID
    outcome: Outcome | None
    finish: str


@dataclass(frozen=True, slots=True)
class FinishedMatch:
    """Данные ``MatchFinished``, нужные рейтингу."""

    match_id: UUID
    kind: str
    ranked: bool
    aborted: bool
    ladder: str | None
    queue_format: str | None
    finished_at: datetime
    players: tuple[MatchPlayer, ...]


class RateResult(StrEnum):
    """Итог обработки матча."""

    RATED = "rated"
    SKIPPED = "skipped"
    DUPLICATE = "duplicate"


def _is_rated(match: FinishedMatch) -> bool:
    return match.ranked and not match.aborted and match.kind == DUEL_KIND


class RateMatch:
    """Рейтинги, история, недельный прирост и ``RatingChanged`` — одной транзакцией.

    Транзакцию открывает consumer (SAVEPOINT поверх отметки inbox): повтор события отсекает
    inbox, повтор того же матча в другом событии — ``processed_matches``.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        boards: Leaderboards,
        *,
        max_future_skew: timedelta,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._boards = boards
        self._max_future_skew = max_future_skew

    async def execute(self, match: FinishedMatch) -> RateResult:
        """Обработать матч.

        Raises:
            MatchContractError: данные матча противоречат правилам (повтор бессмыслен).
            NoActiveSeasonError: у ladder-а нет действующего сезона (повторимо).
        """
        if not _is_rated(match):
            return RateResult.SKIPPED
        if match.ladder is None or match.queue_format is None:
            raise MatchContractError("рейтинговый матч без ladder или формата очереди")
        first, second = require_pair(list(match.players))
        outcomes = _paired_outcomes(first, second)
        if match.finished_at > self._clock.now() + self._max_future_skew:
            raise MatchContractError("finished_at матча в будущем")

        async with self._uow() as uow:
            published = await uow.ladders.published(match.ladder)
            if published is None:
                raise MatchContractError(f"ladder {match.ladder!r} не опубликован")
            config = published.config
            if match.queue_format not in config.formats:
                raise MatchContractError("формат очереди не поддерживается ladder-ом")
            now = self._clock.now()
            if not await uow.matches.mark(match.match_id, config.key, now):
                return RateResult.DUPLICATE
            season = await uow.seasons.active(config.key, lock="share")
            if season is None:
                raise NoActiveSeasonError("нет действующего сезона")

            ids = sorted([first.user_id, second.user_id])
            # Новичкам строки создаются до блокировок и в порядке user_id: так два матча одного
            # игрока не затирают друг друга и не ждут друг друга по кругу.
            known = await uow.ratings.many(config.key, season.season_id, ids)
            for user_id in ids:
                if user_id in known:
                    continue
                previous = await uow.ratings.latest_before(user_id, config.key, season.season_id)
                await uow.ratings.ensure(
                    previous.carry_over(season.season_id, config.season.soft_reset, now)
                    if previous is not None
                    else Rating.start(user_id, season.season_id, config, now)
                )
            current = await uow.ratings.lock(config.key, season.season_id, ids)

            by_id = {first.user_id: (first, outcomes[0]), second.user_id: (second, outcomes[1])}
            sides = {
                uid: current[uid].side(outcome, left=p.finish in LEAVER_FINISHES)
                for uid, (p, outcome) in by_id.items()
            }
            a_id, b_id = first.user_id, second.user_id
            calc = EloCalculator(config.elo, config.calibration)
            deltas = dict(
                zip(
                    (a_id, b_id),
                    calc.settle(sides[a_id], sides[b_id], match.queue_format),
                    strict=True,
                )
            )
            week = week_start(match.finished_at, config.weekly.timezone)
            if await uow.weekly.is_finalized(config.key, week):
                # Неделя уже объявлена: прирост не теряем и закрытый топ не меняем — он идёт
                # в текущую неделю (решение по ревью G; метрика + лог для разбора).
                counted = week_start(now, config.weekly.timezone)
                WEEKLY_LATE_MATCHES.labels(config.key).inc()
                log.warning(
                    "rating.weekly_late_match",
                    ladder=config.key,
                    week=week.isoformat(),
                    counted_in=counted.isoformat(),
                )
                week = counted
            for uid, rating in current.items():
                event = rating.apply_match(
                    config,
                    deltas[uid],
                    match_id=match.match_id,
                    queue_format=match.queue_format,
                    outcome=by_id[uid][1],
                    at=match.finished_at,
                )
                await uow.ratings.save(rating)
                await uow.history.add(event)
                await uow.weekly.add_delta(
                    config.key, week, uid, season_id=season.season_id, delta=event.delta, now=now
                )
                uow.track(rating)
            await uow.commit()
            ratings = list(current.values())
            deleted = await uow.users.deleted(ids)
        for rating in ratings:
            await self._boards.sync(config, rating, deleted=rating.user_id in deleted)
        log.info("rating.match_rated", ladder=config.key, match_id=str(match.match_id))
        return RateResult.RATED


def _paired_outcomes(a: MatchPlayer, b: MatchPlayer) -> tuple[Outcome, Outcome]:
    """Исходы дуэли должны быть согласованы: победа/поражение или две ничьи."""
    if a.user_id == b.user_id:
        raise MatchContractError("один игрок дважды в матче")
    if a.outcome is None or b.outcome is None:
        raise MatchContractError("в дуэли у каждого игрока должен быть исход")
    consistent = {
        (Outcome.WIN, Outcome.LOSS),
        (Outcome.LOSS, Outcome.WIN),
        (Outcome.DRAW, Outcome.DRAW),
    }
    if (a.outcome, b.outcome) not in consistent:
        raise MatchContractError("исходы игроков дуэли не согласованы")
    return a.outcome, b.outcome
