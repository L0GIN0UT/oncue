"""Периодические задачи сервиса (план этапа 2, §2.3).

Задачи крутятся в процессе **relay** — он есть у каждого сервиса с БД и запускается в
``all``, поэтому compose и ``gv gen`` не меняются. Каждый запуск задачи идёт под
``pg_try_advisory_xact_lock(crc32("<service>:<job>"))``: если задачу прямо сейчас выполняет
другая реплика, этот тик пропускается (``result="skipped"``). Блокировка держится открытой
транзакцией на отдельном соединении, пока выполняется ``run``.

Гарантия — взаимное исключение, а не «ровно раз за интервал»: две реплики с разными фазами
таймеров могут выполнить задачу дважды за интервал. Поэтому задачи обязаны быть
идемпотентными (очистка по сроку, планирование с UNIQUE и т. п.).

Ошибка задачи пишется в лог и метрику, цикл продолжается: упавшая очистка не должна
останавливать публикацию outbox в том же процессе.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

import structlog
from prometheus_client import Counter, Histogram
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core.random import Random, SystemRandom

log = structlog.get_logger(__name__)

_JOB_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")

PERIODIC_JOB_RUNS = Counter(
    "gv_periodic_job_runs_total", "Запуски периодических задач", ["job", "result"]
)
PERIODIC_JOB_DURATION = Histogram(
    "gv_periodic_job_duration_seconds", "Длительность периодической задачи", ["job"]
)


@dataclass(frozen=True, slots=True)
class PeriodicJob:
    """Периодическая задача.

    Attributes:
        name: Имя (``outbox.purge_published``); вместе с именем сервиса даёт ключ блокировки.
        interval_s: Пауза между запусками в одной реплике.
        run: Сама задача; открывает свои сессии/Unit of Work, должна быть идемпотентной.
        jitter_s: Случайная добавка к паузе (и задержка первого запуска), чтобы реплики и
            сервисы не стартовали задачи одновременно.
    """

    name: str
    interval_s: float
    run: Callable[[], Awaitable[object]]
    jitter_s: float = 0.0

    def __post_init__(self) -> None:
        if not _JOB_NAME.match(self.name):
            raise ValueError(f"недопустимое имя задачи: {self.name!r}")
        if self.interval_s <= 0 or self.jitter_s < 0:
            raise ValueError("interval_s > 0, jitter_s >= 0")


class JobResult(StrEnum):
    """Итог одного тика задачи (метка ``result`` метрики)."""

    OK = "ok"
    ERROR = "error"
    SKIPPED = "skipped"


def job_lock_key(service: str, job: str) -> int:
    """Ключ advisory lock задачи: стабильный crc32 от ``"<service>:<job>"``."""
    return zlib.crc32(f"{service}:{job}".encode())


class PeriodicJobRunner:
    """Запуск периодических задач одного сервиса.

    Args:
        service: Имя сервиса (часть ключа блокировки).
        jobs: Задачи; имена уникальны.
        sessions: Фабрика сессий БД сервиса (для блокировки).
        random: Источник джиттера.
    """

    def __init__(
        self,
        service: str,
        jobs: Sequence[PeriodicJob],
        sessions: async_sessionmaker[AsyncSession],
        *,
        random: Random | None = None,
    ) -> None:
        names = [j.name for j in jobs]
        if len(set(names)) != len(names):
            raise ValueError(f"имена периодических задач повторяются: {sorted(names)}")
        self._service = service
        self._jobs = {j.name: j for j in jobs}
        self._sessions = sessions
        self._random = random or SystemRandom()

    @property
    def jobs(self) -> list[PeriodicJob]:
        """Зарегистрированные задачи."""
        return list(self._jobs.values())

    async def run_once(self, name: str) -> JobResult:
        """Выполнить задачу один раз, если её не выполняет другая реплика.

        Raises:
            KeyError: нет задачи с таким именем.
        """
        job = self._jobs[name]
        async with self._sessions() as session, session.begin():
            acquired = await session.scalar(
                select(func.pg_try_advisory_xact_lock(job_lock_key(self._service, name)))
            )
            if not acquired:
                PERIODIC_JOB_RUNS.labels(name, JobResult.SKIPPED).inc()
                return JobResult.SKIPPED
            started = time.perf_counter()
            try:
                await job.run()
            except Exception:
                result = JobResult.ERROR
                log.exception("periodic_job.failed", job=name)
            else:
                result = JobResult.OK
            PERIODIC_JOB_DURATION.labels(name).observe(time.perf_counter() - started)
        PERIODIC_JOB_RUNS.labels(name, result).inc()
        if result is JobResult.OK:
            log.debug("periodic_job.done", job=name)
        return result

    def _pause(self, job: PeriodicJob, *, first: bool) -> float:
        jitter = self._random.random() * job.jitter_s
        return jitter if first else job.interval_s + jitter

    async def _loop(self, job: PeriodicJob, stop: asyncio.Event) -> None:
        first = True
        while not stop.is_set():
            if await _wait(stop, self._pause(job, first=first)):
                return
            first = False
            try:
                await self.run_once(job.name)
            except Exception:
                # Например, БД недоступна для блокировки: пробуем на следующем тике.
                PERIODIC_JOB_RUNS.labels(job.name, JobResult.ERROR).inc()
                log.exception("periodic_job.iteration_failed", job=job.name)

    async def run_forever(self, stop: asyncio.Event) -> None:
        """Крутить все задачи до сигнала остановки (текущий запуск дорабатывает)."""
        if not self._jobs:
            return
        log.info("periodic_jobs.started", jobs=sorted(self._jobs))
        async with asyncio.TaskGroup() as group:
            for job in self._jobs.values():
                group.create_task(self._loop(job, stop), name=f"periodic:{job.name}")
        log.info("periodic_jobs.stopped")


async def _wait(stop: asyncio.Event, seconds: float) -> bool:
    """Подождать ``seconds``; ``True`` — пришёл сигнал остановки."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    return stop.is_set()
