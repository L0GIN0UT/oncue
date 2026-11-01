"""Награда за соло-сессию по ``game.GameFinished`` (ADR 0042, 0043; R2, R3, R7)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.accrual.common import config_at
from progression_service.application.accrual.posting import Poster, local_day
from progression_service.application.accrual.ugc_guard import NO_COLLECTION, decide
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.domain import experience, wallet

log = structlog.get_logger(__name__)

SOLO_REASON = "solo_round"


class AccrualResult(StrEnum):
    """Итог обработки события."""

    ACCRUED = "accrued"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class SoloSession:
    """Данные ``GameFinished``, нужные начислению.

    Attributes:
        round_points: Очки сыгранных раундов (раунды ``void`` отсеяны).
        day: День дейлика; не ``None`` — награда только по ``DailyCompleted``.
    """

    session_id: UUID
    user_id: UUID | None
    day: date | None
    finished_at: datetime
    round_points: tuple[int, ...]
    collection_id: UUID | None = None


class AccrueSolo:
    """Опыт и Версы за раунды соло-сессии одной транзакцией.

    Гость и дейлик пропускаются. Суммы режутся дневным потолком; счётчик берётся под
    блокировкой строки, сутки — по ``finished_at`` в зоне конфига.
    """

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def execute(self, session: SoloSession) -> AccrualResult:
        """Начислить за сессию.

        Raises:
            ConfigUnavailableError: нет опубликованного конфига (повторимо).
        """
        if session.user_id is None or session.day is not None or not session.round_points:
            return AccrualResult.SKIPPED
        user_id = session.user_id
        async with self._uow() as uow:
            published = await config_at(uow, session.finished_at)
            cfg = published.config
            poster = Poster(uow, published, self._clock.now())
            await poster.lock_user(user_id)
            day = local_day(session.finished_at, published)
            counters = await uow.counters.lock(user_id, day)
            ref = str(session.session_id)

            xp_op = f"game.session:{ref}:xp"
            verse_op = f"game.session:{ref}:verse"
            xp = verses = 0
            xp_pending = not await poster.accounted(xp_op)
            verse_pending = not await poster.accounted(verse_op)
            # Защита от накрутки (ugc_guard): свой сборник и повтор в окне наград не дают.
            decision = NO_COLLECTION
            if session.collection_id is not None and (xp_pending or verse_pending):
                decision = await decide(
                    uow, cfg.ugc_guard, user_id, (session.collection_id,), session.finished_at
                )
            reason = SOLO_REASON if decision.allowed else decision.verdict.mark_reason
            if xp_pending:
                raw_xp = experience.solo_session_xp(cfg.xp, session.round_points)
                capped = (
                    experience.apply_cap(raw_xp, counters.solo_xp, cfg.xp.solo_daily_cap)
                    if decision.allowed
                    else 0
                )
                ugc_cap = cfg.ugc_guard.ugc_daily_xp_cap
                if decision.ugc and ugc_cap is not None:
                    capped = experience.apply_cap(capped, counters.ugc_xp, ugc_cap)
                xp = await poster.xp(
                    user_id,
                    op_id=xp_op,
                    base_amount=capped,
                    reason=SOLO_REASON,
                    source_ref=ref,
                )
                await poster.mark_if_capped(
                    user_id, op_id=xp_op, raw=raw_xp, paid=xp, reason=reason
                )
            if verse_pending:
                raw_verses = wallet.solo_session_verses(cfg.verses, len(session.round_points))
                capped = (
                    experience.apply_cap(
                        raw_verses, counters.solo_verses, cfg.verses.solo_daily_cap
                    )
                    if decision.allowed
                    else 0
                )
                verses = await poster.verses(
                    user_id,
                    op_id=verse_op,
                    amount=capped,
                    reason=SOLO_REASON,
                    source_ref=ref,
                )
                await poster.mark_if_capped(
                    user_id, op_id=verse_op, raw=raw_verses, paid=verses, reason=reason
                )
            counters.solo_xp += xp
            counters.solo_verses += verses
            if decision.ugc:
                counters.ugc_xp += xp
            await uow.counters.save(user_id, day, counters)
            await uow.commit()
        log.info("progression.solo_accrued", session_id=ref, xp=xp, verses=verses)
        return AccrualResult.ACCRUED
