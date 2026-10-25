"""Периодические задачи progression (relay, под advisory lock; идемпотентны).

* ``progression.ledger.reconcile`` — сверка «сумма журнала = баланс / накопленный опыт»
  (ADR 0043): расхождение пишется в лог уровня ERROR и в метрику, данные не правятся;
  сверка инкрементальная (контрольная точка в БД + полный проход раз в сутки, ADR 0062);
* ``progression.retention.purge`` — удаление старых ``metric_marks`` / ``quest_progress`` пачками
  (журналы не чистятся, ADR 0062);
* ``progression.config.bootstrap`` — версия 1 конфига из ``GV_PROGRESSION__BOOTSTRAP_CONFIG_DIR``
  (только local/ci), если опубликованной версии нет;
* ``progression.events.tick`` — статусы запусков сезонных событий по времени и события
  ``EventRunStarted`` / ``EventRunFinished`` (ADR 0054).
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml
from prometheus_client import Counter, Gauge

from gv_service import Environment, Infrastructure, PeriodicJob
from progression_service.application.admin.common import ConfigDraftInvalidError
from progression_service.application.admin.configs import ConfigCommands
from progression_service.application.reconcile import IncrementalReconciler
from progression_service.application.retention import RetentionPurger
from progression_service.infrastructure.container import container_for

log = structlog.get_logger(__name__)

RECONCILE_JOB = "progression.ledger.reconcile"
RETENTION_JOB = "progression.retention.purge"
BOOTSTRAP_JOB = "progression.config.bootstrap"
EVENTS_TICK_JOB = "progression.events.tick"
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


RETENTION_DELETED = Counter(
    "gv_progression_retention_deleted_total",
    "Строки, удалённые ретенцией (журналы не чистятся)",
    ["table"],
)


def _wall_ms() -> int:
    """Системное время (мс): в этой же шкале ``uuid7()`` метит проводки журналов."""
    return time.time_ns() // 1_000_000


def build_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Задачи сервиса (без БД — нет задач)."""
    if infra.sessions is None:
        return []
    c = container_for(infra)
    s = c.settings.progression
    reconciler = IncrementalReconciler(
        c.uow_factory(),
        infra.clock,
        limit=s.reconcile_limit,
        statement_timeout_ms=s.reconcile_statement_timeout_ms,
        full_every=timedelta(seconds=s.reconcile_full_every_s),
        lag=timedelta(seconds=s.reconcile_lag_s),
        batch=s.reconcile_batch,
        now_ms=_wall_ms,
    )
    purger = RetentionPurger(
        c.uow_factory(),
        infra.clock,
        marks_days=s.retention_metric_marks_days,
        quest_progress_days=s.retention_quest_progress_days,
        batch=s.retention_batch,
        pause_s=s.retention_pause_s,
    )

    async def reconcile() -> None:
        result = await reconciler.run()
        found = result.mismatches
        # Открытые расхождения полного прохода не гаснут между инкрементальными запусками.
        LEDGER_MISMATCHES.set(result.outstanding)
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

    async def retention() -> None:
        result = await purger.run()
        RETENTION_DELETED.labels("metric_marks").inc(result.metric_marks)
        RETENTION_DELETED.labels("quest_progress").inc(result.quest_progress)

    async def events_tick() -> None:
        await c.event_ticker.run()

    jobs = [
        PeriodicJob(RECONCILE_JOB, s.reconcile_interval_s, reconcile, s.reconcile_jitter_s),
        PeriodicJob(RETENTION_JOB, s.retention_interval_s, retention, s.retention_jitter_s),
        PeriodicJob(EVENTS_TICK_JOB, s.events_tick_interval_s, events_tick, s.events_tick_jitter_s),
    ]
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
