"""Периодические задачи game (выполняются в relay под advisory lock, ``gv_service.periodic``).

* ``game.daily.schedule`` — наборы дня вперёд и блокировка наступивших дней;
* ``game.sessions.abandon`` — брошенные сессии;
* ``game.modes.bootstrap`` — сиды режимов из ``GV_GAME__BOOTSTRAP_MODES_DIR`` (только local/ci).

Все задачи идемпотентны: повтор или запуск в двух репликах ничего не дублирует.
"""

from __future__ import annotations

from game_service.infrastructure.container import GameContainer
from gv_service import Environment, PeriodicJob

BOOTSTRAP_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.CI})


def build_jobs(container: GameContainer) -> list[PeriodicJob]:
    """Задачи сервиса."""
    g = container.settings.game

    async def schedule() -> None:
        await container.schedule.execute()

    async def abandon() -> None:
        await container.play.abandon_stale(g.abandon_batch)

    jobs = [
        PeriodicJob("game.daily.schedule", g.schedule_interval_s, schedule, g.schedule_jitter_s),
        PeriodicJob("game.sessions.abandon", g.abandon_interval_s, abandon, g.abandon_jitter_s),
    ]
    directory = g.bootstrap_modes_dir
    if directory is not None and container.settings.environment in BOOTSTRAP_ENVIRONMENTS:

        async def bootstrap() -> None:
            await container.bootstrap.execute(directory)
            # Режимы появились — сразу создаём наборы дня, не дожидаясь своего тика.
            await container.schedule.execute()

        jobs.append(PeriodicJob("game.modes.bootstrap", g.bootstrap_interval_s, bootstrap, 0.0))
    return jobs
