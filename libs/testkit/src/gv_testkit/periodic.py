"""Запуск периодических задач сервиса в тестах (``gv_service.PeriodicJob``).

Задача выполняется так же, как в процессе relay — под advisory lock и со встроенными
задачами очистки, — но один раз и без таймеров.
"""

from __future__ import annotations

from gv_service.builtin_jobs import collect_periodic_jobs
from gv_service.definition import Infrastructure, ServiceDefinition
from gv_service.periodic import JobResult, PeriodicJobRunner


async def run_periodic_job(
    definition: ServiceDefinition, name: str, infra: Infrastructure
) -> JobResult:
    """Выполнить задачу ``name`` сервиса один раз.

    Args:
        definition: Описание сервиса (его ``build_periodic_jobs`` + встроенные задачи).
        name: Имя задачи, например ``outbox.purge_published``.
        infra: Инфраструктура с БД (``Infrastructure.open`` с ``GV_DATABASE_URL``).

    Raises:
        RuntimeError: у инфраструктуры нет БД.
        KeyError: нет задачи с таким именем.
    """
    if infra.sessions is None:
        raise RuntimeError("периодические задачи требуют БД (infra.sessions)")
    jobs = collect_periodic_jobs(definition, infra)
    runner = PeriodicJobRunner(infra.settings.service_name, jobs, infra.sessions)
    return await runner.run_once(name)
