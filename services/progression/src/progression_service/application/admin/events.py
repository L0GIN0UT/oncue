"""Admin-API 5d: сезонные события - определения, запуски, карта, статистика (ADR 0054-0056).

Каждая мутация - одна транзакция: ключ идемпотентности -> проверки -> запись -> аудит
(``admin_actions``). Публикация запуска берёт advisory-lock и кладёт неизменяемый снимок
определения; остановка сразу публикует ``EventRunFinished(reason = stopped)``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import InvalidInputError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from progression_service.application.admin.common import AdminContext, audit
from progression_service.application.admin.ports import AdminUnitOfWork, AdminUowFactory, Json
from progression_service.application.ports.events import EventRow, RunRow
from progression_service.application.ports.shop import MediaInspector, MediaStore, TokenSource
from progression_service.domain import shop
from progression_service.domain.events import EventRunFinished
from progression_service.domain.events_season import (
    STORED_LIVE,
    Definition,
    EventBodyChangedError,
    EventDefinitionInvalidError,
    EventDefinitionLockedError,
    EventKeyTakenError,
    EventNotFoundError,
    EventPeriodInvalidError,
    EventPeriodOverlapError,
    EventRunNotFoundError,
    EventRunStateInvalidError,
    RunStatus,
    canonical_sha256,
    definition_errors,
    parse_definition,
    run_status,
)
from progression_service.domain.shop import MediaKind

log = structlog.get_logger(__name__)


class EventMapInvalidError(InvalidInputError):
    """Файл карты не подходит (422)."""

    default_code = "progression.event_map_invalid"

    def __init__(self, reason: str) -> None:
        super().__init__(f"карта не подходит: {reason}")


class EventMapTooLargeError(InvalidInputError):
    """Файл карты больше лимита (на HTTP - 413, обрабатывается в ручке)."""

    default_code = "progression.event_map_too_large"

    def __init__(self) -> None:
        super().__init__("файл карты больше допустимого размера")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def run_json(run: RunRow, now: datetime) -> Json:
    """Запуск для admin-API (``AdminRun``; ``active/finished`` - по времени)."""
    return {
        "id": str(run.id),
        "seq": run.seq,
        "status": run_status(run.status, run.starts_at, run.ends_at, now).value,
        "starts_at": _iso(run.starts_at),
        "ends_at": _iso(run.ends_at),
        "published_at": _iso(run.published_at),
        "stopped_at": _iso(run.stopped_at),
    }


async def _run(
    factory: AdminUowFactory,
    fingerprint: str,
    ctx: AdminContext,
    perform: Callable[[AdminUnitOfWork], Awaitable[Json]],
    *,
    status_code: int = 200,
) -> tuple[Json, bool]:
    """Идемпотентное выполнение в одной транзакции (ключ - ``(актор, Idempotency-Key)``)."""
    async with factory() as uow:
        response, executed = await run_idempotent(
            uow.idempotency,
            ctx.actor_id,
            ctx.idempotency_key,
            fingerprint,
            lambda: perform(uow),
            status_code=status_code,
        )
        await uow.commit()
    return response, executed


async def _check_definition(uow: AdminUnitOfWork, event_key: str, definition: Definition) -> None:
    """Правила определения и каталог: слоты триггеров, предмет основной награды.

    Raises:
        EventDefinitionInvalidError: 422 ``progression.event_definition_invalid``.
    """
    published = await uow.configs.published()
    shop_cfg = None if published is None else published.config.shop
    slot_keys = None if shop_cfg is None else {s.key for s in shop_cfg.slots}
    errors = definition_errors(definition, slot_keys=slot_keys)
    if definition.item_key is not None:
        item = await uow.items.get_by_key(definition.item_key)
        if item is None or not item.active:
            errors.append({"path": "reward.item_key", "reason": "нет активного предмета"})
        elif item.event_key != event_key:
            errors.append(
                {"path": "reward.item_key", "reason": "предмет не привязан к этому событию"}
            )
    if errors:
        raise EventDefinitionInvalidError(errors)


class EventAdminCommands:
    """Определения, карта, запуски."""

    def __init__(
        self,
        factory: AdminUowFactory,
        clock: Clock,
        *,
        store: MediaStore,
        inspector: MediaInspector,
        tokens: TokenSource,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._store = store
        self._inspector = inspector
        self._tokens = tokens

    def event_json(self, row: EventRow, runs: list[RunRow], now: datetime) -> Json:
        """Событие для admin-API (``AdminEvent``)."""
        return {
            "key": row.key,
            "body": row.body,
            "body_sha256": row.body_sha256,
            "map_url": None if row.map_key is None else self._store.public_url(row.map_key),
            "runs": [run_json(r, now) for r in runs],
            "updated_at": row.updated_at.isoformat(),
        }

    async def create(self, key: str, body: Json, *, ctx: AdminContext) -> Json:
        """Создать событие.

        Raises:
            EventKeyTakenError: ключ занят (409).
            EventDefinitionInvalidError: определение нарушает правила (422).
        """
        fingerprint = request_fingerprint("event.create", key, {"body": body, "reason": ctx.reason})

        async def perform(uow: AdminUnitOfWork) -> Json:
            await _check_definition(uow, key, parse_definition(body))
            now = self._clock.now()
            row = EventRow(uuid7(), key, body, canonical_sha256(body), None, ctx.actor_id, now, now)
            if not await uow.events.add_event(row):
                raise EventKeyTakenError
            await audit(uow, ctx, "event.create", f"event:{key}", now, action_id=uuid7())
            return self.event_json(row, [], now)

        response, _ = await _run(self._factory, fingerprint, ctx, perform, status_code=201)
        return response

    async def update(self, key: str, body: Json, *, ctx: AdminContext) -> Json:
        """Заменить определение (пока нет запуска ``published/active``).

        Raises:
            EventNotFoundError: нет события (404).
            EventDefinitionLockedError: есть опубликованный запуск (409).
            EventDefinitionInvalidError: определение нарушает правила (422).
        """
        fingerprint = request_fingerprint("event.update", key, {"body": body, "reason": ctx.reason})

        async def perform(uow: AdminUnitOfWork) -> Json:
            event = await uow.events.get_event(key, for_update=True)
            if event is None:
                raise EventNotFoundError
            now = self._clock.now()
            if await uow.events.has_locking_run(event.id, now):
                raise EventDefinitionLockedError
            await _check_definition(uow, key, parse_definition(body))
            updated = replace(event, body=body, body_sha256=canonical_sha256(body), updated_at=now)
            await uow.events.save_event(updated)
            await audit(uow, ctx, "event.update", f"event:{key}", now, action_id=uuid7())
            return self.event_json(updated, await uow.events.runs_of(event.id), now)

        response, _ = await _run(self._factory, fingerprint, ctx, perform)
        return response

    async def upload_map(self, key: str, data: bytes, *, ctx: AdminContext) -> Json:
        """Загрузить карту: статика PNG/WebP, лимиты ``shop.media`` (как у предметов).

        Raises:
            EventNotFoundError: нет события (404).
            EventMapTooLargeError: файл больше лимита (413 на HTTP).
            EventMapInvalidError: файл не подходит (422).
        """
        digest = hashlib.sha256(data).hexdigest()
        fingerprint = request_fingerprint("event.map", key, {"reason": ctx.reason, "sha": digest})
        uploaded: list[str] = []
        stale: list[str] = []

        async def perform(uow: AdminUnitOfWork) -> Json:
            event = await uow.events.get_event(key, for_update=True)
            if event is None:
                raise EventNotFoundError
            published = await uow.configs.published()
            shop_cfg = None if published is None else published.config.shop
            if shop_cfg is None:
                raise EventMapInvalidError("магазин не настроен в конфиге")
            try:
                inspected = self._inspector.inspect(data, MediaKind.STATIC, shop_cfg.media)
                shop.check_media(inspected.info, MediaKind.STATIC, shop_cfg.media)
            except shop.ItemMediaTooLargeError as exc:
                raise EventMapTooLargeError from exc
            except shop.ItemMediaInvalidError as exc:
                raise EventMapInvalidError(exc.message) from exc
            map_key = shop.object_key(self._tokens.token_hex(), inspected.extension)
            await self._store.put(map_key, data, inspected.content_type)
            uploaded.append(map_key)
            now = self._clock.now()
            updated = replace(event, map_key=map_key, updated_at=now)
            await uow.events.save_event(updated)
            if event.map_key is not None:
                stale.append(event.map_key)
            await audit(uow, ctx, "event.map", f"event:{key}", now, action_id=uuid7())
            return self.event_json(updated, await uow.events.runs_of(event.id), now)

        try:
            response, executed = await _run(self._factory, fingerprint, ctx, perform)
        except Exception:
            await self._discard(uploaded)  # транзакция откатилась: новый объект не нужен
            raise
        if executed:
            await self._discard(stale)
        return response

    async def create_run(
        self, key: str, starts_at: datetime, ends_at: datetime, *, ctx: AdminContext
    ) -> Json:
        """Черновик запуска (и перезапуск) с периодом.

        Raises:
            EventNotFoundError: нет события (404).
            EventPeriodInvalidError: конец не позже начала или уже в прошлом (422).
        """
        fingerprint = request_fingerprint(
            "event.run.create",
            key,
            {"starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(), "r": ctx.reason},
        )

        async def perform(uow: AdminUnitOfWork) -> Json:
            event = await uow.events.get_event(key, for_update=True)
            if event is None:
                raise EventNotFoundError
            now = self._clock.now()
            if ends_at <= starts_at:
                raise EventPeriodInvalidError("конец периода должен быть позже начала")
            if ends_at <= now:
                raise EventPeriodInvalidError("период уже закончился")
            draft = RunRow(
                uuid7(), event.id, key, 0, RunStatus.DRAFT, starts_at, ends_at, None, None, None,
                None, ctx.actor_id, now,
            )  # fmt: skip
            run = await uow.events.add_run(draft)
            await audit(
                uow, ctx, "event.run.create", f"event:{key}", now, action_id=uuid7(),
                run_id=str(run.id),
            )  # fmt: skip
            return run_json(run, now)

        response, _ = await _run(self._factory, fingerprint, ctx, perform, status_code=201)
        return response

    async def publish_run(
        self, key: str, run_id: UUID, body_sha256: str, *, ctx: AdminContext
    ) -> Json:
        """Опубликовать запуск: снимок определения, хеш, проверка пересечений.

        Raises:
            EventNotFoundError, EventRunNotFoundError: 404.
            EventRunStateInvalidError: не черновик или период закончился (409).
            EventBodyChangedError: хеш определения другой (409).
            EventPeriodOverlapError: период пересекается с другим запуском (409).
            EventDefinitionInvalidError: определение больше не проходит проверки (422).
        """
        fingerprint = request_fingerprint(
            "event.run.publish", run_id, {"sha": body_sha256, "reason": ctx.reason}
        )

        async def perform(uow: AdminUnitOfWork) -> Json:
            await uow.events.lock_publishing()  # пересечения проверяются по очереди
            event = await uow.events.get_event(key, for_update=True)
            if event is None:
                raise EventNotFoundError
            run = await uow.events.get_run(run_id, for_update=True)
            if run is None or run.event_id != event.id:
                raise EventRunNotFoundError
            now = self._clock.now()
            if run.status != RunStatus.DRAFT:
                raise EventRunStateInvalidError("опубликовать можно только черновик")
            if run.ends_at <= now:
                raise EventRunStateInvalidError("период запуска уже закончился")
            if event.body_sha256 != body_sha256:
                raise EventBodyChangedError
            await _check_definition(uow, key, parse_definition(event.body))
            if await uow.events.overlaps(run.starts_at, run.ends_at, exclude=run.id):
                raise EventPeriodOverlapError
            published = replace(
                run,
                status=RunStatus.PUBLISHED,
                snapshot=event.body,
                body_sha256=event.body_sha256,
                published_at=now,
            )
            await uow.events.save_run(published)
            await audit(
                uow, ctx, "event.run.publish", f"event:{key}", now, action_id=uuid7(),
                run_id=str(run_id), body_sha256=body_sha256,
            )  # fmt: skip
            return run_json(published, now)

        response, _ = await _run(self._factory, fingerprint, ctx, perform)
        return response

    async def stop_run(self, key: str, run_id: UUID, *, ctx: AdminContext) -> Json:
        """Остановить активный или опубликованный запуск; ``EventRunFinished(stopped)`` сразу.

        Raises:
            EventRunNotFoundError: нет запуска события (404).
            EventRunStateInvalidError: запуск не опубликован или уже закончился (409).
        """
        fingerprint = request_fingerprint("event.run.stop", run_id, {"reason": ctx.reason})

        async def perform(uow: AdminUnitOfWork) -> Json:
            run = await uow.events.get_run(run_id, for_update=True)
            if run is None or run.event_key != key:
                raise EventRunNotFoundError
            now = self._clock.now()
            status = run_status(run.status, run.starts_at, run.ends_at, now)
            if (
                status not in (RunStatus.PUBLISHED, RunStatus.ACTIVE)
                or run.status not in STORED_LIVE
            ):
                raise EventRunStateInvalidError("остановить можно опубликованный или идущий запуск")
            stopped = replace(run, status=RunStatus.STOPPED, stopped_at=now)
            await uow.events.save_run(stopped)
            uow.publish(
                EventRunFinished(
                    aggregate_id=str(run.id),
                    run_id=run.id,
                    event_key=key,
                    reason="stopped",
                    at=now,
                )
            )
            await audit(
                uow, ctx, "event.run.stop", f"event:{key}", now, action_id=uuid7(),
                run_id=str(run_id),
            )  # fmt: skip
            return run_json(stopped, now)

        response, _ = await _run(self._factory, fingerprint, ctx, perform)
        return response

    async def _discard(self, keys: list[str]) -> None:
        """Удалить объекты; сбой хранилища не ломает ответ (объект - мусор, не данные)."""
        try:
            await self._store.delete(keys)
        except Exception:
            log.warning("progression.event_map_cleanup_failed", count=len(keys), exc_info=True)


class EventAdminQueries:
    """Список, карточка, статистика."""

    def __init__(
        self, factory: AdminUowFactory, clock: Clock, commands: EventAdminCommands
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._commands = commands

    async def all(self) -> Json:
        """Список событий с последним запуском."""
        async with self._factory() as uow:
            now = self._clock.now()
            events = await uow.events.list_events()
            latest = await uow.events.latest_runs()
        items = []
        for e in events:
            run = latest.get(e.id)
            title = e.body.get("title", e.key)
            items.append(
                {
                    "key": e.key,
                    "title": title,
                    "status": None if run is None else run_json(run, now)["status"],
                    "latest_run": None if run is None else run_json(run, now),
                }
            )
        return {"items": items}

    async def get(self, key: str) -> Json:
        """Событие с запусками.

        Raises:
            EventNotFoundError: нет события (404).
        """
        async with self._factory() as uow:
            event = await uow.events.get_event(key)
            if event is None:
                raise EventNotFoundError
            runs = await uow.events.runs_of(event.id)
        return self._commands.event_json(event, runs, self._clock.now())

    async def stats(self, key: str, run_id: UUID) -> Json:
        """Статистика запуска.

        Raises:
            EventRunNotFoundError: нет запуска события (404).
        """
        async with self._factory() as uow:
            run = await uow.events.get_run(run_id)
            if run is None or run.event_key != key:
                raise EventRunNotFoundError
            players, completed, counts = await uow.events.stats(run_id)
        unlocked = {c.find_key: c.unlocked for c in counts}
        finds: list[Any] = []
        if run.snapshot is not None:
            finds = [
                {"find_key": f.key, "number": f.number, "unlocked": unlocked.get(f.key, 0)}
                for f in parse_definition(run.snapshot).finds
            ]
        return {"run_id": str(run_id), "players": players, "completed": completed, "finds": finds}
