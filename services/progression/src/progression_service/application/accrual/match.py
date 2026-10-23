"""Награда за онлайн-матч по ``match.MatchFinished`` (ADR 0042, 0043; R4, R5, R7).

Ranked и кастом равны, рейтинг на опыт не влияет. Прерванный матч, выход и ``forfeit`` — 0;
``tech_win`` — участие и раунды без бонуса за победу. Все игроки матча — одной транзакцией
в порядке ``user_id`` (одинаковый порядок блокировок у параллельных матчей).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import AccrualContractError, config_at
from progression_service.application.accrual.posting import Poster, local_day
from progression_service.application.accrual.solo import AccrualResult
from progression_service.application.ports import (
    NoopRewardHook,
    RewardHook,
    UnitOfWorkFactory,
)
from progression_service.domain import experience, wallet
from progression_service.domain.experience import Finish, MatchFormat, OnlineResult, Outcome

log = structlog.get_logger(__name__)

FIRST_WIN_REASON = "first_win"
RoundScores = tuple[tuple[UUID, int], ...]
"""Очки игроков в одном раунде: ``(user_id, score)``."""


@dataclass(frozen=True, slots=True)
class MatchPlayerResult:
    """Игрок завершённого матча."""

    user_id: UUID
    finish: Finish
    outcome: Outcome | None
    place: int


@dataclass(frozen=True, slots=True)
class FinishedMatch:
    """Данные ``MatchFinished``, нужные начислению."""

    match_id: UUID
    format: MatchFormat
    aborted: bool
    finished_at: datetime
    players: tuple[MatchPlayerResult, ...]
    rounds: tuple[RoundScores, ...]


def rounds_stats(rounds: tuple[RoundScores, ...], user_id: UUID) -> tuple[int, int]:
    """``(сыграно, выиграно)`` раундов игрока.

    Сыграно — игрок есть в результатах раунда. Выигран — очки строго больше, чем у каждого
    другого игрока раунда, и больше нуля.
    """
    played = won = 0
    for scores in rounds:
        mine = [s for u, s in scores if u == user_id]
        if not mine:
            continue
        played += 1
        best_other = max((s for u, s in scores if u != user_id), default=0)
        if mine[0] > best_other:
            won += 1
    return played, won


class AccrueMatch:
    """Опыт и Версы игрокам матча одной транзакцией."""

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, hook: RewardHook | None = None
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._hook: RewardHook = hook or NoopRewardHook()

    async def execute(self, match: FinishedMatch) -> AccrualResult:
        """Начислить игрокам матча.

        Raises:
            AccrualContractError: один игрок дважды в матче.
            ConfigUnavailableError: нет опубликованного конфига (повторимо).
        """
        ids = [p.user_id for p in match.players]
        if len(set(ids)) != len(ids):
            raise AccrualContractError("игрок дважды в одном матче")
        if match.aborted:
            return AccrualResult.SKIPPED
        async with self._uow() as uow:
            published = await config_at(uow, match.finished_at)
            cfg = published.config
            poster = Poster(uow, published, self._clock.now())
            day = local_day(match.finished_at, published)
            ref = str(match.match_id)
            reason = "duel" if match.format is MatchFormat.DUEL else "party"
            for player in sorted(match.players, key=lambda p: p.user_id):
                user_id = player.user_id
                played, won = rounds_stats(match.rounds, user_id)
                result = OnlineResult(
                    format=match.format,
                    finish=player.finish,
                    outcome=player.outcome,
                    place=player.place,
                    rounds_total=len(match.rounds),
                    rounds_played=played,
                    rounds_won=won,
                )
                online = experience.online_xp(cfg.xp, result, cfg.min_rounds_ratio)
                await poster.lock_user(user_id)
                counters = await uow.counters.lock(user_id, day)
                xp_op = f"match:{ref}:{user_id}:xp"
                if not await uow.ledger.has_xp(xp_op):
                    multiplier = (
                        await self._hook.online_xp_multiplier(user_id, match.match_id)
                        if online.match_xp > 0 or online.first_win_eligible
                        else Decimal(1)
                    )
                    await poster.xp(
                        user_id,
                        op_id=xp_op,
                        base_amount=online.match_xp,
                        reason=reason,
                        source_ref=ref,
                        multiplier=multiplier,
                    )
                    first_win = online.first_win_eligible and cfg.xp.first_win_bonus > 0
                    if first_win and not counters.first_win_awarded:
                        await poster.xp(
                            user_id,
                            op_id=f"first_win:{user_id}:{day.isoformat()}",
                            base_amount=cfg.xp.first_win_bonus,
                            reason=FIRST_WIN_REASON,
                            source_ref=ref,
                            multiplier=multiplier,
                        )
                        counters.first_win_awarded = True
                verse_op = f"match:{ref}:{user_id}:verse"
                if not await poster.accounted(verse_op):
                    raw = wallet.online_verses(cfg.verses, result, cfg.min_rounds_ratio)
                    paid = await poster.verses(
                        user_id,
                        op_id=verse_op,
                        amount=wallet.cap_online_verses(cfg.verses, raw, counters.online_verses),
                        reason=reason,
                        source_ref=ref,
                    )
                    await poster.mark_if_capped(
                        user_id, op_id=verse_op, raw=raw, paid=paid, reason=reason
                    )
                    counters.online_verses += paid
                await uow.counters.save(user_id, day, counters)
            await uow.commit()
        log.info("progression.match_accrued", match_id=ref, players=len(match.players))
        return AccrualResult.ACCRUED
