"""Задача ``progression.events.tick``: материализует статусы запусков и публикует события.

``published → active`` (``EventRunStarted``), ``active → finished`` (``EventRunFinished``,
``reason = ended``). Чтение статуса от задачи не зависит (считается по времени, ADR 0054), поэтому
расхождение допустимо до следующего прогона. Идемпотентность - условный ``UPDATE`` статуса: события
уходят в outbox только вместе с реальной сменой статуса в той же транзакции.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from progression_service.application.ports import UnitOfWorkFactory
from progression_service.domain.events import EventRunFinished, EventRunStarted
from progression_service.domain.events_season import RunStatus, parse_definition

from gv_core.clock import Clock

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TickResult:
    """Сколько запусков начато и закончено за прогон."""

    started: int
    finished: int


class EventTicker:
    """Один прогон задачи."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def run(self) -> TickResult:
        """Перевести статусы по времени; повтор без изменений времени ничего не делает."""
        started = finished = 0
        async with self._uow() as uow:
            now = self._clock.now()
            for run in await uow.events.to_activate(now):
                if not await uow.events.set_status(
                    run.id, RunStatus.ACTIVE, expected=(RunStatus.PUBLISHED,)
                ):
                    continue
                assert run.snapshot is not None  # noqa: S101 - published всегда со снимком
                definition = parse_definition(run.snapshot)
                uow.publish(
                    EventRunStarted(
                        aggregate_id=str(run.id),
                        run_id=run.id,
                        event_key=run.event_key,
                        title=definition.title,
                        theme_key=definition.theme_key,
                        starts_at=run.starts_at,
                        ends_at=run.ends_at,
                    )
                )
                started += 1
            for run in await uow.events.to_finish(now):
                if not await uow.events.set_status(
                    run.id,
                    RunStatus.FINISHED,
                    expected=(RunStatus.PUBLISHED, RunStatus.ACTIVE),
                ):
                    continue
                uow.publish(
                    EventRunFinished(
                        aggregate_id=str(run.id),
                        run_id=run.id,
                        event_key=run.event_key,
                        reason="ended",
                        at=now,
                    )
                )
                finished += 1
            await uow.commit()
        if started or finished:
            log.info("progression.events_tick", started=started, finished=finished)
        return TickResult(started, finished)
