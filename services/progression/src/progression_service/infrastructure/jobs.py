"""Периодические задачи progression (relay, под advisory lock; идемпотентны).

* ``progression.ledger.reconcile`` — сверка «сумма журнала = баланс / накопленный опыт»
  (ADR 0043): расхождение пишется в лог уровня ERROR и в метрику, данные не правятся.
"""

from __future__ import annotations

import structlog
from prometheus_client import Counter, Gauge

from gv_service import Infrastructure, PeriodicJob
from progression_service.application.reconcile import LedgerReconciler
from progression_service.infrastructure.container import container_for

log = structlog.get_logger(__name__)

RECONCILE_JOB = "progression.ledger.reconcile"

LEDGER_MISMATCHES = Gauge(
    "gv_progression_ledger_mismatches",
    "Число расхождений журнала с балансом/опытом в последней сверке (алерт при > 0)",
)
LEDGER_MISMATCH_FOUND = Counter(
    "gv_progression_ledger_mismatch_total", "Найденные расхождения журнала", ["kind"]
)


def build_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Задачи сервиса (без БД — нет задач)."""
    if infra.sessions is None:
        return []
    c = container_for(infra)
    s = c.settings.progression
    reconciler = LedgerReconciler(
        c.uow_factory(),
        limit=s.reconcile_limit,
        statement_timeout_ms=s.reconcile_statement_timeout_ms,
    )

    async def reconcile() -> None:
        found = await reconciler.run()
        LEDGER_MISMATCHES.set(len(found))
        for m in found:
            LEDGER_MISMATCH_FOUND.labels(m.kind).inc()
            # user_id — внутренний идентификатор, не PII; суммы нужны для разбора.
            log.error(
                "progression.ledger_mismatch",
                kind=m.kind,
                user_id=str(m.user_id),
                bucket=m.bucket,
                ledger_sum=m.ledger_sum,
                stored=m.stored,
            )

    return [PeriodicJob(RECONCILE_JOB, s.reconcile_interval_s, reconcile, s.reconcile_jitter_s)]
