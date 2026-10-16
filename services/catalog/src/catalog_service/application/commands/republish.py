"""Переопубликация единиц (``POST /admin/v1/items/republish``, план этапа 3a M9, D9).

Потребители (game, media-pipeline) заполняют новые поля (метрики, тексты, страны) для уже
известных единиц только из нового события: событие с ``version`` не больше применённой они
отбрасывают. Поэтому каждая единица выпускается заново с ``version+1``.

Поток: ``RequestRepublish`` (идемпотентно по ``Idempotency-Key``, аудит) создаёт задание и
ставит RabbitMQ-задачу ``republish`` с курсором ``None`` → воркер ``RunRepublishBatch``
переопубликует порцию ``GV_CATALOG__REPUBLISH_BATCH`` единиц (по возрастанию id) в одной
транзакции с outbox, сдвигает курсор и ставит следующую порцию. Задача порции несёт свой
стартовый курсор: повтор доставки уже обработанной порции ничего не делает.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import structlog

from catalog_service.application.commands.admin import AdminContext, run_admin_mutation
from catalog_service.application.ports import TaskQueue, UnitOfWorkFactory
from catalog_service.domain.model import MediaDomain
from catalog_service.domain.republish import RepublishJob
from gv_core.ids import uuid7

log = structlog.get_logger(__name__)

REPUBLISH_TASK = "republish"
"""Тип RabbitMQ-задачи порции переопубликации; ``task_id = <job_id>:<курсор|start>``."""


# Цель действия в аудите и отпечатке запроса — «домен каталога» (стабильный uuid5): id
# задания новый при каждом вызове и сломал бы сверку повтора по Idempotency-Key; сам id
# задания хранится в ответе (``admin_actions.response``).
_REPUBLISH_TARGET_NS = uuid.UUID("0192a3b4-c5d6-7e8f-9a0b-4e9b0b115400")


def _target(domain: MediaDomain | None) -> uuid.UUID:
    return uuid.uuid5(_REPUBLISH_TARGET_NS, domain.value if domain else "all")


def _task_id(job_id: uuid.UUID, after: uuid.UUID | None) -> str:
    return f"{job_id}:{after or 'start'}"


async def _schedule(queue: TaskQueue, job_id: uuid.UUID, after: uuid.UUID | None) -> None:
    await queue.enqueue(
        REPUBLISH_TASK,
        {"job_id": str(job_id), "after": str(after) if after else None},
        task_id=_task_id(job_id, after),
    )


class RequestRepublish:
    """Поставить переопубликацию единиц домена (право ``catalog.import``).

    Задача ставится до коммита (как генерация в media-pipeline): если коммит не случится,
    воркер не найдёт задание и задача станет no-op; обратный порядок мог бы оставить
    задание без задачи.
    """

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        tasks: TaskQueue,
        new_id: Callable[[], uuid.UUID] = uuid7,
    ) -> None:
        self._uow = uow_factory
        self._tasks = tasks
        self._new_id = new_id

    async def __call__(self, domain: MediaDomain | None, ctx: AdminContext) -> dict[str, Any]:
        """Вернуть ``{"job_id"}`` (тот же — при повторе с тем же ключом)."""
        job_id: uuid.UUID = self._new_id()
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                job = RepublishJob(job_id, domain=domain, actor_id=ctx.actor_id, reason=ctx.reason)
                await uow.republish.add(job)
                await _schedule(self._tasks, job.id, None)
                return {"job_id": str(job.id)}

            response, done = await run_admin_mutation(
                uow,
                ctx,
                action="items.republish",
                target_id=_target(domain),
                body={"domain": domain.value if domain else None, "reason": ctx.reason},
                perform=perform,
            )
            await uow.commit()
        if done:
            log.info(
                "catalog.republish_requested",
                job_id=response["job_id"],
                domain=domain.value if domain else None,
            )
        return response


class RunRepublishBatch:
    """Переопубликовать порцию единиц задания (обработчик задачи ``republish``)."""

    def __init__(self, uow_factory: UnitOfWorkFactory, tasks: TaskQueue, *, batch: int) -> None:
        if batch < 1:
            raise ValueError("batch должен быть положительным")
        self._uow = uow_factory
        self._tasks = tasks
        self._batch = batch

    async def __call__(self, job_id: uuid.UUID, after: uuid.UUID | None) -> int:
        """Обработать порцию; вернуть число переопубликованных единиц (0 — повтор/нет задания)."""
        async with self._uow() as uow:
            job = await uow.republish.get(job_id)
            if job is None or not job.expects(after):
                log.info("catalog.republish_batch_skipped", job_id=str(job_id))
                return 0
            ids = await uow.items.list_ids_after(job.domain, after, self._batch)
            for item_id in ids:
                item = await uow.items.get(item_id)
                if item is None:
                    continue
                item.republish()
                await uow.items.save(item)
                uow.track(item)
            done = len(ids) < self._batch
            job.advance(ids, done=done)
            await uow.republish.save(job)
            if not done:
                await _schedule(self._tasks, job.id, job.cursor)
            await uow.commit()
        log.info(
            "catalog.republish_batch_done",
            job_id=str(job_id),
            items=len(ids),
            published=job.published,
            done=done,
        )
        return len(ids)
