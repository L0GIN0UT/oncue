"""Запуск периодических задач сервиса в тестах (``gv_service.PeriodicJob``).

Задача выполняется так же, как в процессе relay — под advisory lock и со встроенными
задачами очистки, — но один раз и без таймеров.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import insert, select

from gv_core.ids import uuid7
from gv_db.idempotency import IDEMPOTENCY_TABLE
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


async def assert_idempotency_purge(definition: ServiceDefinition, infra: Infrastructure) -> None:
    """Проверить встроенную задачу ``idempotency.purge`` сервиса на его схеме.

    Кладёт в ``idempotency_keys`` ключ старше ``GV_IDEMPOTENCY__RETENTION_H`` и свежий,
    выполняет задачу (под advisory lock, как relay) и проверяет, что удалён только старый.

    Raises:
        AssertionError: у сервиса нет задачи/таблицы или удалено не то.
    """
    if infra.sessions is None or infra.tables is None:
        raise RuntimeError("проверка очистки требует БД (infra.sessions, infra.tables)")
    table = infra.tables.metadata.tables.get(IDEMPOTENCY_TABLE)
    assert table is not None, f"у {definition.name} нет таблицы {IDEMPOTENCY_TABLE}"
    names = [j.name for j in collect_periodic_jobs(definition, infra)]
    assert "idempotency.purge" in names, f"у {definition.name} нет idempotency.purge: {names}"

    now = infra.clock.now()
    retention = timedelta(hours=infra.settings.idempotency.retention_h)
    stale, fresh = uuid7().hex, uuid7().hex
    async with infra.sessions() as session, session.begin():
        for key, created_at in ((stale, now - retention - timedelta(minutes=1)), (fresh, now)):
            await session.execute(
                insert(table).values(
                    actor_id=uuid7(),
                    key=key,
                    request_hash="0" * 64,
                    response={},
                    status_code=200,
                    created_at=created_at,
                )
            )
    result = await run_periodic_job(definition, "idempotency.purge", infra)
    assert result is JobResult.OK, result
    async with infra.sessions() as session:
        left = set((await session.execute(select(table.c.key))).scalars().all())
    assert stale not in left, "просроченный ключ идемпотентности не удалён"
    assert fresh in left, "свежий ключ идемпотентности удалён"
