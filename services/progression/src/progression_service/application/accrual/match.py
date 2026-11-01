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
from progression_service.application.accrual.posting import Poster, boosted, local_day
from progression_service.application.accrual.solo import AccrualResult
from progression_service.application.accrual.ugc_guard import NO_COLLECTION, decide
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
    collection_ids: tuple[UUID, ...] = ()
    """Сборники матча (лобби): для защиты от накрутки через пользовательские сборники."""


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


def _fit_cap(base: int, multiplier: Decimal, remaining: int) -> int:
    """Наибольшая база не больше ``base``, чей опыт с множителем влезает в ``remaining``."""
    fitted = max(0, min(base, remaining))
    while fitted > 0 and boosted(fitted, multiplier) > remaining:
        fitted -= 1
    return fitted


class AccrueMatch:
    """Опыт и Версы игрокам матча одной транзакцией."""

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, hook: RewardHook | None = None
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._hook: RewardHook = hook or NoopRewardHook()

    async def execute(self, match: FinishedMatch) -> AccrualResult:  # noqa: PLR0915 — один проход по игрокам в одной транзакции
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
                verse_op = f"match:{ref}:{user_id}:verse"
                xp_pending = not await poster.accounted(xp_op)
                verse_pending = not await poster.accounted(verse_op)
                # Защита от накрутки (ugc_guard): свой сборник и повтор в окне наград не дают.
                decision = NO_COLLECTION
                if match.collection_ids and (xp_pending or verse_pending):
                    decision = await decide(
                        uow, cfg.ugc_guard, user_id, match.collection_ids, match.finished_at
                    )
                guard_reason = decision.verdict.mark_reason
                if xp_pending and not decision.allowed:
                    await poster.mark_if_capped(
                        user_id, op_id=xp_op, raw=online.match_xp, paid=0, reason=guard_reason
                    )
                elif xp_pending:
                    multiplier = (
                        await self._hook.online_xp_multiplier(
                            uow, user_id, match.match_id, self._clock.now()
                        )
                        if online.match_xp > 0
                        or (online.first_win_eligible and cfg.xp.first_win_bonus > 0)
                        else Decimal(1)
                    )
                    ugc_cap = cfg.ugc_guard.ugc_daily_xp_cap if decision.ugc else None
                    match_xp = online.match_xp
                    if ugc_cap is not None:
                        match_xp = _fit_cap(match_xp, multiplier, ugc_cap - counters.ugc_xp)
                    paid_xp = await poster.xp(
                        user_id,
                        op_id=xp_op,
                        base_amount=match_xp,
                        reason=reason,
                        source_ref=ref,
                        multiplier=multiplier,
                    )
                    if decision.ugc:
                        counters.ugc_xp += paid_xp
                    first_win = online.first_win_eligible and cfg.xp.first_win_bonus > 0
                    if first_win and not counters.first_win_awarded:
                        bonus = cfg.xp.first_win_bonus
                        if ugc_cap is not None:
                            bonus = _fit_cap(bonus, multiplier, ugc_cap - counters.ugc_xp)
                        paid_bonus = await poster.xp(
                            user_id,
                            op_id=f"first_win:{user_id}:{day.isoformat()}",
                            base_amount=bonus,
                            reason=FIRST_WIN_REASON,
                            source_ref=ref,
                            multiplier=multiplier,
                        )
                        counters.first_win_awarded = True
                        if decision.ugc:
                            counters.ugc_xp += paid_bonus
                if verse_pending:
                    raw = wallet.online_verses(cfg.verses, result, cfg.min_rounds_ratio)
                    capped_verses = (
                        wallet.cap_online_verses(cfg.verses, raw, counters.online_verses)
                        if decision.allowed
                        else 0
                    )
                    paid = await poster.verses(
                        user_id,
                        op_id=verse_op,
                        amount=capped_verses,
                        reason=reason,
                        source_ref=ref,
                    )
                    await poster.mark_if_capped(
                        user_id,
                        op_id=verse_op,
                        raw=raw,
                        paid=paid,
                        reason=reason if decision.allowed else guard_reason,
                    )
                    counters.online_verses += paid
                await uow.counters.save(user_id, day, counters)
            await uow.commit()
        log.info("progression.match_accrued", match_id=ref, players=len(match.players))
        return AccrualResult.ACCRUED
