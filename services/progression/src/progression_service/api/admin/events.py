"""Admin API 5d: сезонные события (``docs/plans/stage-5d-api.md``, ADR 0054-0056).

Шаг 0a: ручки объявлены, реализация - поток E1; до тех пор 501.

Права: ``admin.access`` + ``progression.events.manage`` (всё, кроме публикации и остановки) или
``progression.events.publish`` (публикация, остановка). Мутации - ``Idempotency-Key`` и
``reason`` (в аудит); ответы без кэша.
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile

from gv_auth import Principal, require_perm
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Reason, problem_responses
from progression_service.api.admin.events_schemas import (
    AdminEvent,
    AdminEventList,
    AdminRun,
    CreateEventRequest,
    CreateRunRequest,
    PublishRunRequest,
    RunStats,
    StopRunRequest,
    UpdateEventRequest,
)

ADMIN_ACCESS = "admin.access"
EventsManager = Annotated[
    Principal, Depends(require_perm(ADMIN_ACCESS, "progression.events.manage"))
]
EventsPublisher = Annotated[
    Principal, Depends(require_perm(ADMIN_ACCESS, "progression.events.publish"))
]
KeyPath = Annotated[
    str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Ключ события")
]
RunPath = Annotated[UUID, Path(description="Идентификатор запуска")]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоке E1 этапа 5d")


def register_event_admin_routes(router: APIRouter) -> None:
    """Добавить в ``/admin/v1`` ручки событий (пока заглушки)."""

    @router.get(
        "/events",
        response_model=AdminEventList,
        summary="События",
        responses=problem_responses(401, 403, 501),
    )
    async def events(principal: EventsManager) -> AdminEventList:
        raise _stub()

    @router.post(
        "/events",
        response_model=AdminEvent,
        status_code=201,
        summary="Создать событие",
        description=(
            "Ошибки: 409 `progression.event_key_taken`, 422 `progression.event_definition_invalid` "
            "(`details.errors` - пути и причины: одна финальная находка, уникальные ключи и "
            "номера, предмет награды есть в каталоге и т. п.)."
        ),
        responses=problem_responses(401, 403, 409, 422, 501),
    )
    async def create_event(
        body: CreateEventRequest, principal: EventsManager, key: IdempotencyKey
    ) -> AdminEvent:
        raise _stub()

    @router.get(
        "/events/{event_key}",
        response_model=AdminEvent,
        summary="Событие",
        description="Определение, хеш, карта и запуски. Ошибки: 404 `progression.event_not_found`.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def get_event(event_key: KeyPath, principal: EventsManager) -> AdminEvent:
        raise _stub()

    @router.put(
        "/events/{event_key}",
        response_model=AdminEvent,
        summary="Изменить определение",
        description=(
            "Ошибки: 404 `progression.event_not_found`, 409 `progression.event_definition_locked` "
            "(есть запуск `published/active`), 422 `progression.event_definition_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def update_event(
        event_key: KeyPath, body: UpdateEventRequest, principal: EventsManager, key: IdempotencyKey
    ) -> AdminEvent:
        raise _stub()

    @router.put(
        "/events/{event_key}/map",
        response_model=AdminEvent,
        summary="Загрузить карту события",
        description=(
            "multipart: `file`, `reason`. Статика PNG/WebP; сигнатура, декодирование и лимиты "
            "- как у `shop.media`. Ошибки: 404 `progression.event_not_found`, 413 "
            "`progression.event_map_too_large`, 422 `progression.event_map_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 413, 422, 501),
    )
    async def upload_map(
        event_key: KeyPath,
        principal: EventsManager,
        key: IdempotencyKey,
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Картинка карты (PNG или WebP)")],
    ) -> AdminEvent:
        raise _stub()

    @router.post(
        "/events/{event_key}/runs",
        response_model=AdminRun,
        status_code=201,
        summary="Создать запуск",
        description=(
            "Черновик запуска с периодом; так же делается перезапуск. Ошибки: 404 "
            "`progression.event_not_found`, 422 `progression.event_period_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def create_run(
        event_key: KeyPath, body: CreateRunRequest, principal: EventsManager, key: IdempotencyKey
    ) -> AdminRun:
        raise _stub()

    @router.post(
        "/events/{event_key}/runs/{run_id}/publish",
        response_model=AdminRun,
        summary="Опубликовать запуск",
        description=(
            "Кладёт неизменяемый снимок определения. Ошибки: 404 `progression.event_not_found` / "
            "`progression.event_run_not_found`, 409 `progression.event_body_changed` (хеш "
            "определения иной), 409 `progression.event_period_overlap`, 409 "
            "`progression.event_run_state_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def publish_run(
        event_key: KeyPath,
        run_id: RunPath,
        body: PublishRunRequest,
        principal: EventsPublisher,
        key: IdempotencyKey,
    ) -> AdminRun:
        raise _stub()

    @router.post(
        "/events/{event_key}/runs/{run_id}/stop",
        response_model=AdminRun,
        summary="Остановить запуск",
        description=(
            "Активный или опубликованный запуск становится `stopped`. Ошибки: 404 "
            "`progression.event_run_not_found`, 409 `progression.event_run_state_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 501),
    )
    async def stop_run(
        event_key: KeyPath,
        run_id: RunPath,
        body: StopRunRequest,
        principal: EventsPublisher,
        key: IdempotencyKey,
    ) -> AdminRun:
        raise _stub()

    @router.get(
        "/events/{event_key}/runs/{run_id}/stats",
        response_model=RunStats,
        summary="Статистика запуска",
        description="Игроки, закончившие событие, и число открывших каждую находку.",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def run_stats(event_key: KeyPath, run_id: RunPath, principal: EventsManager) -> RunStats:
        raise _stub()
