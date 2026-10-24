"""Открытие находки и закрытие события игроком (ADR 0055, 0056).

Всё в транзакции вызывающего (под блокировкой ``progress`` игрока, ``Poster.lock_user``): прогресс
находки, проводки опыта и Версов (``op_id = event:{run}:{find}:{user}``), события outbox
(``FindUnlocked``, ``EventCompleted``) и, при закрытии всех находок, основной предмет
(``source = event``) с нулевой отметкой ``event:{run}:complete:{user}``.
"""

# Контекст транзакции (uow, poster, состояние игрока) передаётся явно.
# ruff: noqa: PLR0917
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

import structlog
from progression_service.application.accrual.posting import Poster
from progression_service.application.ports import ProgressionUnitOfWork
from progression_service.application.ports.events import ProgressRow, RunRow
from progression_service.domain.events import EventCompleted, FindUnlocked, RewardPayload
from progression_service.domain.events_season import (
    REWARD_REASON,
    SERVER,
    Definition,
    FindDef,
    complete_op_id,
    find_op_id,
    others_done,
)
from progression_service.domain.inventory import InventoryItem, Source

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class PlayerRun:
    """Прогресс игрока в запуске: строки по (находка, период) и открытые находки."""

    run: RunRow
    definition: Definition
    user_id: UUID
    rows: dict[tuple[str, str], ProgressRow] = field(default_factory=dict)
    unlocked: set[str] = field(default_factory=set)

    @classmethod
    async def load(
        cls, uow: ProgressionUnitOfWork, run: RunRow, definition: Definition, user_id: UUID
    ) -> PlayerRun:
        """Прочитать прогресс игрока (после ``lock_user``)."""
        state = cls(run, definition, user_id)
        for row in await uow.events.progress_of(run.id, user_id):
            state.rows[(row.find_key, row.period_key)] = row
            if row.unlocked_at is not None:
                state.unlocked.add(row.find_key)
        return state

    def progress_of(self, find_key: str, period_key: str) -> int:
        """Прогресс находки за период."""
        row = self.rows.get((find_key, period_key))
        return 0 if row is None else row.progress

    def best_progress(self, find_key: str) -> int:
        """Наибольший прогресс находки по любым периодам."""
        return max((r.progress for (k, _), r in self.rows.items() if k == find_key), default=0)


async def save_progress(
    uow: ProgressionUnitOfWork,
    state: PlayerRun,
    find: FindDef,
    period_key: str,
    progress: int,
    now: datetime,
) -> None:
    """Записать прогресс без открытия."""
    await uow.events.save_progress(
        state.run.id, state.user_id, find.key, period_key, progress, None, now
    )
    state.rows[(find.key, period_key)] = ProgressRow(find.key, period_key, progress, None)


async def unlock_find(
    uow: ProgressionUnitOfWork,
    poster: Poster,
    state: PlayerRun,
    find: FindDef,
    period_key: str,
    progress: int,
    now: datetime,
) -> bool:
    """Открыть находку: награда, события, закрытие события при последней находке.

    Returns:
        ``False`` - находка уже открыта (ничего не сделано).
    """
    if find.key in state.unlocked:
        return False
    run, user = state.run, state.user_id
    await uow.events.save_progress(run.id, user, find.key, period_key, progress, now, now)
    state.rows[(find.key, period_key)] = ProgressRow(find.key, period_key, progress, now)
    state.unlocked.add(find.key)
    op = find_op_id(run.id, find.key, user)
    ref = f"{run.event_key}:{find.key}"
    await poster.xp(user, op_id=op, base_amount=find.xp, reason=REWARD_REASON, source_ref=ref)
    await poster.verses(user, op_id=op, amount=find.verses, reason=REWARD_REASON, source_ref=ref)
    found = len(state.unlocked)
    uow.publish(
        FindUnlocked(
            aggregate_id=str(user),
            run_id=run.id,
            event_key=run.event_key,
            user_id=user,
            find_key=find.key,
            find_title=find.title,
            found=found,
            total=state.definition.total,
            reward=RewardPayload(xp=find.xp, verses=find.verses),
            at=now,
        )
    )
    log.info("progression.event_find_unlocked", event_key=run.event_key, find=find.key, found=found)
    if found >= state.definition.total:
        await _complete(uow, state, now)
    else:
        await _cascade_final(uow, poster, state, now)
    return True


async def _cascade_final(
    uow: ProgressionUnitOfWork, poster: Poster, state: PlayerRun, now: datetime
) -> None:
    """Остальные открыты, а у финала с серверным триггером цель уже достигнута - открыть его."""
    definition = state.definition
    if not others_done(definition, state.unlocked):
        return
    final = next((f for f in definition.finds if f.final), None)
    if final is None or final.key in state.unlocked or final.trigger.type != SERVER:
        return
    best = [
        r for (k, _), r in state.rows.items() if k == final.key and r.progress >= _target(final)
    ]
    if best:
        await unlock_find(uow, poster, state, final, best[0].period_key, best[0].progress, now)


def _target(find: FindDef) -> int:
    assert find.trigger.target is not None  # noqa: S101 - у server цель есть
    return find.trigger.target


async def _complete(uow: ProgressionUnitOfWork, state: PlayerRun, now: datetime) -> None:
    run, user = state.run, state.user_id
    if not await uow.events.add_completion(run.id, user, now):
        return
    await uow.marks.add(complete_op_id(run.id, user), user, REWARD_REASON, now)
    key = state.definition.item_key
    if key is not None:
        item = await uow.items.get_by_key(key)
        if item is None:
            log.error("progression.event_item_missing", event_key=run.event_key, item_key=key)
        else:
            # Дубль предмета (уже есть) - не повторная выдача: ON CONFLICT DO NOTHING.
            await uow.inventory.add(InventoryItem(user, item.id, Source.EVENT, now, None, None))
    uow.publish(
        EventCompleted(
            aggregate_id=str(user),
            run_id=run.id,
            event_key=run.event_key,
            event_title=state.definition.title,
            user_id=user,
            item_key=key,
            at=now,
        )
    )
    log.info("progression.event_completed", event_key=run.event_key)
