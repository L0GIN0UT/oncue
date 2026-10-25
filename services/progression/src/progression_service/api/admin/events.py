"""Admin API 5d: сезонные события (``docs/plans/stage-5d-api.md``, ADR 0054-0056).

Права: ``admin.access`` + ``progression.events.manage`` (всё, кроме публикации и остановки) или
``progression.events.publish`` (публикация, остановка). Мутации - ``Idempotency-Key`` и
``reason`` (в аудит); успешные ответы - ``Cache-Control: no-store`` (ставится после выполнения).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки права.
# ruff: noqa: ARG001, PLR0917
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Response, UploadFile
from fastapi.responses import JSONResponse

from gv_auth import Principal, require_perm
from gv_service import IdempotencyKey, Reason, problem_responses
from gv_service.problems import problem
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
from progression_service.application.admin import AdminServices
from progression_service.application.admin.common import AdminContext
from progression_service.application.admin.events import EventMapTooLargeError
from progression_service.domain.config import BOUNDS

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
NO_STORE = "no-store"
_UPLOAD_CEILING = int(BOUNDS["shop.media.static_max_bytes"])
"""Наибольшая карта, которую ручка читает в память: граница схемы конфига для статики. Точный
лимит берётся из действующего конфига при разборе."""


def _ctx(principal: Principal, reason: str, key: str) -> AdminContext:
    return AdminContext(actor_id=principal.user_id, reason=reason, idempotency_key=key)


def _too_large(error: EventMapTooLargeError) -> JSONResponse:
    """413 Problem: у ``AppError`` нет статуса 413, поэтому собираем ответ сами."""
    result = problem(413, error.code, error.message)
    result.headers["Cache-Control"] = NO_STORE
    return result


def register_event_admin_routes(router: APIRouter, services: AdminServices) -> None:
    """Добавить в ``/admin/v1`` ручки событий."""

    @router.get(
        "/events",
        response_model=AdminEventList,
        summary="События",
        responses=problem_responses(401, 403),
    )
    async def events(principal: EventsManager, response: Response) -> AdminEventList:
        result = await services.event_queries.all()
        response.headers["Cache-Control"] = NO_STORE
        return AdminEventList.model_validate(result)

    @router.post(
        "/events",
        response_model=AdminEvent,
        status_code=201,
        summary="Создать событие",
        description=(
            "Ошибки: 409 `progression.event_key_taken`, 422 `progression.event_definition_invalid` "
            "(`details.errors` - пути и причины: одна финальная находка, уникальные ключи и "
            "номера, предмет награды - активный предмет этого события и т. п.)."
        ),
        responses=problem_responses(401, 403, 409, 422),
    )
    async def create_event(
        body: CreateEventRequest, principal: EventsManager, key: IdempotencyKey, response: Response
    ) -> AdminEvent:
        result = await services.events.create(
            body.key, body.body.model_dump(mode="json", exclude_unset=True),
            ctx=_ctx(principal, body.reason, key),
        )  # fmt: skip
        response.headers["Cache-Control"] = NO_STORE
        return AdminEvent.model_validate(result)

    @router.get(
        "/events/{event_key}",
        response_model=AdminEvent,
        summary="Событие",
        description="Определение, хеш, карта и запуски. Ошибки: 404 `progression.event_not_found`.",
        responses=problem_responses(401, 403, 404),
    )
    async def get_event(
        event_key: KeyPath, principal: EventsManager, response: Response
    ) -> AdminEvent:
        result = await services.event_queries.get(event_key)
        response.headers["Cache-Control"] = NO_STORE
        return AdminEvent.model_validate(result)

    @router.put(
        "/events/{event_key}",
        response_model=AdminEvent,
        summary="Изменить определение",
        description=(
            "Ошибки: 404 `progression.event_not_found`, 409 `progression.event_definition_locked` "
            "(есть запуск `published/active`), 409 `progression.event_definition_changed` "
            "(задан `expected_body_sha256`, а определение уже другое), "
            "422 `progression.event_definition_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def update_event(
        event_key: KeyPath,
        body: UpdateEventRequest,
        principal: EventsManager,
        key: IdempotencyKey,
        response: Response,
    ) -> AdminEvent:
        result = await services.events.update(
            event_key, body.body.model_dump(mode="json", exclude_unset=True),
            ctx=_ctx(principal, body.reason, key),
            expected_body_sha256=body.expected_body_sha256,
        )  # fmt: skip
        response.headers["Cache-Control"] = NO_STORE
        return AdminEvent.model_validate(result)

    @router.put(
        "/events/{event_key}/map",
        response_model=AdminEvent,
        summary="Загрузить карту события",
        description=(
            "multipart: `file`, `reason`. Статика PNG/WebP; сигнатура, декодирование и лимиты "
            "- как у `shop.media`. Ошибки: 404 `progression.event_not_found`, 413 "
            "`progression.event_map_too_large`, 422 `progression.event_map_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 413, 422),
    )
    async def upload_map(
        event_key: KeyPath,
        principal: EventsManager,
        key: IdempotencyKey,
        response: Response,
        reason: Annotated[Reason, Form()],
        file: Annotated[UploadFile, File(description="Картинка карты (PNG или WebP)")],
    ) -> AdminEvent | JSONResponse:
        data = await file.read(_UPLOAD_CEILING + 1)
        if len(data) > _UPLOAD_CEILING:
            return _too_large(EventMapTooLargeError())
        try:
            result = await services.events.upload_map(
                event_key, data, ctx=_ctx(principal, reason, key)
            )
        except EventMapTooLargeError as exc:
            return _too_large(exc)
        response.headers["Cache-Control"] = NO_STORE
        return AdminEvent.model_validate(result)

    @router.post(
        "/events/{event_key}/runs",
        response_model=AdminRun,
        status_code=201,
        summary="Создать запуск",
        description=(
            "Черновик запуска с периодом; так же делается перезапуск. Ошибки: 404 "
            "`progression.event_not_found`, 422 `progression.event_period_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def create_run(
        event_key: KeyPath,
        body: CreateRunRequest,
        principal: EventsManager,
        key: IdempotencyKey,
        response: Response,
    ) -> AdminRun:
        result = await services.events.create_run(
            event_key, body.starts_at, body.ends_at, ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminRun.model_validate(result)

    @router.post(
        "/events/{event_key}/runs/{run_id}/publish",
        response_model=AdminRun,
        summary="Опубликовать запуск",
        description=(
            "Кладёт неизменяемый снимок определения. Ошибки: 404 `progression.event_not_found` / "
            "`progression.event_run_not_found`, 409 `progression.event_body_changed` (хеш "
            "определения иной), 409 `progression.event_period_overlap`, 409 "
            "`progression.event_run_state_invalid`, 422 `progression.event_definition_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def publish_run(
        event_key: KeyPath,
        run_id: RunPath,
        body: PublishRunRequest,
        principal: EventsPublisher,
        key: IdempotencyKey,
        response: Response,
    ) -> AdminRun:
        result = await services.events.publish_run(
            event_key, run_id, body.body_sha256, ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminRun.model_validate(result)

    @router.post(
        "/events/{event_key}/runs/{run_id}/stop",
        response_model=AdminRun,
        summary="Остановить запуск",
        description=(
            "Активный или опубликованный запуск становится `stopped`. Ошибки: 404 "
            "`progression.event_run_not_found`, 409 `progression.event_run_state_invalid`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def stop_run(
        event_key: KeyPath,
        run_id: RunPath,
        body: StopRunRequest,
        principal: EventsPublisher,
        key: IdempotencyKey,
        response: Response,
    ) -> AdminRun:
        result = await services.events.stop_run(
            event_key, run_id, ctx=_ctx(principal, body.reason, key)
        )
        response.headers["Cache-Control"] = NO_STORE
        return AdminRun.model_validate(result)

    @router.get(
        "/events/{event_key}/runs/{run_id}/stats",
        response_model=RunStats,
        summary="Статистика запуска",
        description="Игроки, закончившие событие, и число открывших каждую находку.",
        responses=problem_responses(401, 403, 404),
    )
    async def run_stats(
        event_key: KeyPath, run_id: RunPath, principal: EventsManager, response: Response
    ) -> RunStats:
        result = await services.event_queries.stats(event_key, run_id)
        response.headers["Cache-Control"] = NO_STORE
        return RunStats.model_validate(result)
