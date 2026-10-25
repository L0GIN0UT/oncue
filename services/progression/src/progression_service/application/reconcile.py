"""Сверка журналов с проекциями: сумма проводок должна равняться балансу и накопленному опыту.

``LedgerReconciler`` - полный проход по всему журналу. ``IncrementalReconciler`` (ADR 0062) -
то, что гоняет задача relay: между полными проходами проверяются только игроки с проводками
новее контрольной точки, полный проход раз в ``full_every`` ловит правку в обход журнала.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from progression_service.application.ports import LedgerMismatch, UnitOfWorkFactory
from progression_service.application.ports.ops import Checkpoint

log = structlog.get_logger(__name__)

XP_LEDGER = "xp_ledger"
CURRENCY_LEDGER = "currency_ledger"
LEDGERS: dict[str, str] = {XP_LEDGER: "xp", CURRENCY_LEDGER: "currency"}
"""Журнал -> ``kind`` расхождения в ``LedgerMismatch``."""

_MS_MASK = 0xFFFF_FFFF_FFFF
_UUID7_VERSION = 0x7 << 76
_UUID7_VARIANT = 0b10 << 62


def uuid7_floor(timestamp_ms: int) -> UUID:
    """Наименьший UUID v7 с данным временем: все id, созданные не раньше, не меньше него."""
    return UUID(int=((timestamp_ms & _MS_MASK) << 80) | _UUID7_VERSION | _UUID7_VARIANT)


class LedgerReconciler:
    """Ищет расхождения «журнал ≠ баланс» и «журнал опыта ≠ progress.total_xp» (весь журнал)."""

    def __init__(self, uow: UnitOfWorkFactory, *, limit: int, statement_timeout_ms: int) -> None:
        self._uow = uow
        self._limit = limit
        self._timeout_ms = statement_timeout_ms

    async def run(self) -> list[LedgerMismatch]:
        """Расхождения (не больше ``limit``); транзакция read-only, запрос ограничен по времени."""
        async with self._uow() as uow:
            await uow.read_only(statement_timeout_ms=self._timeout_ms)
            return await uow.ledger.mismatches(self._limit)


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Итог запуска сверки.

    Attributes:
        mismatches: Найденные в этом запуске расхождения.
        full: Был ли это полный проход.
        outstanding: Сколько расхождений открыто (найденные сейчас или последним полным
            проходом): значение для метрики-алерта, не гаснущей между инкрементальными запусками.
    """

    mismatches: list[LedgerMismatch]
    full: bool
    outstanding: int


class IncrementalReconciler:
    """Инкрементальная сверка с контрольной точкой в БД (ADR 0062).

    Контрольная точка - id последней покрытой проводки журнала (uuid7). Она двигается только
    после успешной проверки куска и никогда за границу «сейчас - лаг»: id создаётся до коммита,
    и транзакция с меньшим id может закоммититься позже точки. Сбой на любом шаге оставляет
    точку прежней, повтор проверяет тех же игроков заново (идемпотентно).

    Args:
        uow: Фабрика Unit of Work.
        clock: Часы сервиса (решение «пора ли полный проход»).
        limit: Сколько расхождений читать за один запрос.
        statement_timeout_ms: Потолок времени запроса.
        full_every: Период полного прохода.
        lag: Лаг границы покрытия.
        batch: Размер куска проводок (по id) в одном запросе.
        now_ms: Время (мс) в шкале генератора uuid7: проводки получают id по системным часам,
            поэтому граница считается по ним же, а не по ``clock``.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        limit: int,
        statement_timeout_ms: int,
        full_every: timedelta,
        lag: timedelta,
        batch: int,
        now_ms: Callable[[], int],
    ) -> None:
        if batch < 1 or limit < 1:
            raise ValueError("batch и limit должны быть положительными")
        self._uow = uow
        self._clock = clock
        self._limit = limit
        self._timeout_ms = statement_timeout_ms
        self._full_every = full_every
        self._lag_ms = int(lag.total_seconds() * 1000)
        self._batch = batch
        self._now_ms = now_ms

    async def run(self) -> ReconcileResult:
        """Один запуск: полный проход, если пора, иначе инкрементальный."""
        bound = uuid7_floor(self._now_ms() - self._lag_ms)
        async with self._uow() as uow:
            state = await uow.reconcile.load()
        if self._needs_full(state):
            return await self._full(bound)
        return await self._incremental(state, bound)

    def _needs_full(self, state: dict[str, Checkpoint]) -> bool:
        points = [state.get(name) for name in LEDGERS]
        fulls = [p.last_full_at for p in points if p is not None and p.last_id is not None]
        if len(fulls) < len(LEDGERS) or any(f is None for f in fulls):
            return True
        oldest = min(f for f in fulls if f is not None)
        return self._clock.now() - oldest >= self._full_every

    async def _full(self, bound: UUID) -> ReconcileResult:
        async with self._uow() as uow:
            await uow.read_only(statement_timeout_ms=self._timeout_ms)
            found = await uow.ledger.mismatches(self._limit)
        now = self._clock.now()
        # Полный проход покрыл всё, что было закоммичено; проводки новее ``bound`` ещё раз
        # проверит инкрементальный проход.
        async with self._uow() as uow:
            for name, kind in LEDGERS.items():
                await uow.reconcile.save(
                    name,
                    last_id=bound,
                    now=now,
                    full=True,
                    full_mismatches=sum(1 for m in found if m.kind == kind),
                )
            await uow.commit()
        return ReconcileResult(found, full=True, outstanding=len(found))

    async def _incremental(self, state: dict[str, Checkpoint], bound: UUID) -> ReconcileResult:
        found: dict[tuple[str, UUID, str | None], LedgerMismatch] = {}
        for name in LEDGERS:
            cursor = state[name].last_id
            batch = self._batch
            while True:
                async with self._uow() as uow:
                    await uow.read_only(statement_timeout_ms=self._timeout_ms)
                    upto, size = await uow.ledger.next_chunk(name, cursor, bound, batch)
                    if upto is None:
                        break
                    chunk = await uow.ledger.mismatches_in_range(name, cursor, upto, self._limit)
                if len(chunk) >= self._limit and batch > 1:
                    # Выдача усечена: часть расхождений кусок не показал. Точку не двигаем -
                    # сужаем кусок, пока выдача не поместится в ``limit`` (на одной проводке
                    # затронут один игрок, поэтому сужение конечно).
                    batch = max(1, batch // 2)
                    log.warning(
                        "progression.reconcile_chunk_truncated",
                        ledger=name,
                        limit=self._limit,
                        next_batch=batch,
                    )
                    continue
                for m in chunk:
                    found[(m.kind, m.user_id, m.bucket)] = m
                await self._save(name, upto)
                cursor = upto
                if size < batch:
                    break
                batch = self._batch
            if cursor is not None and cursor < bound:
                await self._save(name, bound)
        mismatches = list(found.values())
        carried = sum(p.full_mismatches for p in state.values())
        return ReconcileResult(mismatches, full=False, outstanding=max(len(mismatches), carried))

    async def _save(self, ledger: str, last_id: UUID) -> None:
        async with self._uow() as uow:
            await uow.reconcile.save(ledger, last_id=last_id, now=self._clock.now())
            await uow.commit()
