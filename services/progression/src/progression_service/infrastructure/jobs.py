"""Периодические задачи progression (relay, под advisory lock; идемпотентны).

* ``progression.ledger.reconcile`` — сверка «сумма журнала = баланс / накопленный опыт»
  (ADR 0043): расхождение пишется в лог уровня ERROR и в метрику, данные не правятся;
* ``progression.config.bootstrap`` — версия 1 конфига из ``GV_PROGRESSION__BOOTSTRAP_CONFIG_DIR``
  (только local/ci), если опубликованной версии нет.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml
from prometheus_client import Counter, Gauge

from gv_service import Environment, Infrastructure, PeriodicJob
from progression_service.application.admin.common import ConfigDraftInvalidError
from progression_service.application.admin.configs import ConfigCommands
from progression_service.application.reconcile import LedgerReconciler
from progression_service.infrastructure.container import container_for

log = structlog.get_logger(__name__)

RECONCILE_JOB = "progression.ledger.reconcile"
BOOTSTRAP_JOB = "progression.config.bootstrap"
BOOTSTRAP_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.CI})
SEED_FILE = "config.yaml"
SEED_ACTOR = UUID(int=0)
"""Системный «автор» сида в аудите (человека за bootstrap нет)."""
SEED_REASON = "bootstrap сидов"

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

    jobs = [PeriodicJob(RECONCILE_JOB, s.reconcile_interval_s, reconcile, s.reconcile_jitter_s)]
    directory = s.bootstrap_config_dir
    if directory is not None and c.settings.environment in BOOTSTRAP_ENVIRONMENTS:

        async def bootstrap() -> None:
            await bootstrap_config(c.admin.configs, directory)

        jobs.append(PeriodicJob(BOOTSTRAP_JOB, s.bootstrap_interval_s, bootstrap, 0.0))
    return jobs


def _read_seed(directory: Path) -> Any:
    """Прочитать ``config.yaml`` (в потоке, вне event loop)."""
    return yaml.safe_load((directory / SEED_FILE).read_text(encoding="utf-8"))


async def bootstrap_config(commands: ConfigCommands, directory: Path) -> bool:
    """Опубликовать версию 1 из сида, если опубликованной версии нет; ``True`` - опубликовали.

    Битый или отсутствующий файл логируется (ERROR) и не роняет задачу.
    """
    try:
        raw = await asyncio.to_thread(_read_seed, directory)
    except (OSError, yaml.YAMLError):
        log.exception("progression.bootstrap_unreadable", directory=str(directory))
        return False
    if not isinstance(raw, dict):
        log.error("progression.bootstrap_invalid_file", file=SEED_FILE)
        return False
    try:
        published = await commands.bootstrap(raw, actor_id=SEED_ACTOR, reason=SEED_REASON)
    except ConfigDraftInvalidError:
        log.exception("progression.bootstrap_config_invalid", file=SEED_FILE)
        return False
    if published:
        log.info("progression.config_bootstrapped")
    return published
