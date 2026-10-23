"""Периодические задачи rating (relay, под advisory lock; все идемпотентны).

* ``rating.seasons.tick`` — закрытие вышедших сезонов, мягкий сброс, старт следующих;
* ``rating.weekly.finalize`` — ``WeeklyTopFinalized`` по прошедшим неделям;
* ``rating.leaderboards.rebuild`` — запросы админа на пересборку и самолечение лидербордов;
* ``rating.ladders.bootstrap`` — сиды ladder-ов из ``GV_RATING__BOOTSTRAP_LADDERS_DIR``
  (только local/ci): создаёт отсутствующие, существующие не трогает.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml

from gv_service import Environment, Infrastructure, PeriodicJob
from rating_service.application.commands.ladders import LadderCommands
from rating_service.domain.errors import LadderInvalidError
from rating_service.infrastructure.container import container_for

log = structlog.get_logger(__name__)

SEASONS_JOB = "rating.seasons.tick"
WEEKLY_JOB = "rating.weekly.finalize"
REBUILD_JOB = "rating.leaderboards.rebuild"
BOOTSTRAP_JOB = "rating.ladders.bootstrap"
BOOTSTRAP_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.CI})
SEED_ACTOR = UUID(int=0)
"""Системный «автор» сидов в аудите (человека за bootstrap нет)."""
SEED_REASON = "bootstrap сидов"


def _read_seeds(directory: Path) -> list[tuple[str, Any]]:
    """Сиды ``*.yaml`` каталога по имени файла (чтение — в потоке, вне event loop)."""
    return [
        (path.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    ]


async def bootstrap_ladders(commands: LadderCommands, directory: Path) -> list[str]:
    """Создать ladder-ы из сидов каталога; вернуть ключи созданных сейчас.

    Битый файл логируется и пропускается: остальные сиды загружаются.
    """
    created: list[str] = []
    for name, raw in await asyncio.to_thread(_read_seeds, directory):
        if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
            log.error("rating.bootstrap_invalid_file", file=name)
            continue
        try:
            if await commands.bootstrap(raw, actor_id=SEED_ACTOR, reason=SEED_REASON):
                created.append(raw["key"])
        except LadderInvalidError:
            log.exception("rating.bootstrap_config_invalid", file=name, ladder=raw["key"])
    if created:
        log.info("rating.ladders_bootstrapped", ladders=created)
    return created


def build_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Задачи сервиса (без БД — нет задач)."""
    if infra.sessions is None:
        return []
    c = container_for(infra)
    s = c.settings.rating

    async def seasons() -> None:
        await c.season_commands.tick()

    async def weekly() -> None:
        await c.weekly.execute()

    async def rebuild() -> None:
        await c.boards.process_requests()
        await c.boards.heal()

    jobs = [
        PeriodicJob(SEASONS_JOB, s.seasons_interval_s, seasons, s.seasons_jitter_s),
        PeriodicJob(WEEKLY_JOB, s.weekly_interval_s, weekly, s.weekly_jitter_s),
        PeriodicJob(REBUILD_JOB, s.rebuild_interval_s, rebuild, s.rebuild_jitter_s),
    ]
    directory = s.bootstrap_ladders_dir
    if directory is not None and c.settings.environment in BOOTSTRAP_ENVIRONMENTS:

        async def bootstrap() -> None:
            await bootstrap_ladders(c.ladder_commands, directory)

        jobs.append(PeriodicJob(BOOTSTRAP_JOB, s.bootstrap_interval_s, bootstrap, 0.0))
    return jobs
