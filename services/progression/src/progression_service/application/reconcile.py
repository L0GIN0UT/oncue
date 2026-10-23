"""Сверка журналов с проекциями: сумма проводок должна равняться балансу и накопленному опыту."""

from __future__ import annotations

from progression_service.application.ports import LedgerMismatch, UnitOfWorkFactory


class LedgerReconciler:
    """Ищет расхождения «журнал ≠ баланс» и «журнал опыта ≠ progress.total_xp»."""

    def __init__(self, uow: UnitOfWorkFactory, *, limit: int, statement_timeout_ms: int) -> None:
        self._uow = uow
        self._limit = limit
        self._timeout_ms = statement_timeout_ms

    async def run(self) -> list[LedgerMismatch]:
        """Расхождения (не больше ``limit``); транзакция read-only, запрос ограничен по времени."""
        async with self._uow() as uow:
            await uow.read_only(statement_timeout_ms=self._timeout_ms)
            return await uow.ledger.mismatches(self._limit)
