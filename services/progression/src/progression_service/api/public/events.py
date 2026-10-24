"""Ручки сезонных событий (5d, ``docs/plans/stage-5d-api.md``, ADR 0054-0056).

``GET /events/active`` - анонимный; остальные - только пользователь с аккаунтом. Ответы с данными
игрока - ``private, no-store``. Находка-элемент интерфейса засчитывается идемпотентно.
"""

# Idempotency-Key обязателен по контракту, но claim идемпотентен и сам по себе.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response

from gv_auth import Principal, require_user
from gv_service import IdempotencyKey, problem_responses
from progression_service.api.public.events_schemas import (
    ActiveEvent,
    ClaimResult,
    EventPage,
    MyEvents,
)
from progression_service.application.events.player import EventPlayerService

User = Annotated[Principal, Depends(require_user)]
KeyPath = Annotated[
    str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Ключ события")
]
FindPath = Annotated[
    str, Path(pattern=r"^[a-z][a-z0-9_]*$", max_length=64, description="Ключ находки")
]
PRIVATE_NO_STORE = "private, no-store"


def register_event_routes(router: APIRouter, events: EventPlayerService) -> None:
    """Добавить в ``/v1`` ручки событий."""

    @router.get(
        "/events/active",
        response_model=ActiveEvent,
        summary="Активное событие",
        description=(
            "Анонимно. Запуск, идущий сейчас, или `event = null`. Нужен шапке (тема) и главной; "
            "bff кэширует ответ до 30 с."
        ),
        responses=problem_responses(),
    )
    async def active_event(response: Response) -> ActiveEvent:
        response.headers["Cache-Control"] = "no-store"
        return ActiveEvent.model_validate(await events.active())

    @router.get(
        "/events/{event_key}",
        response_model=EventPage,
        summary="Страница события",
        description=(
            "Запуск: активный, иначе последний завершённый или остановленный. Закрытая финальная "
            "находка без прогресса триггера. Черновики и неопубликованные не видны: 404 "
            "`progression.event_not_found`."
        ),
        responses=problem_responses(401, 403, 404),
    )
    async def event_page(event_key: KeyPath, user: User, response: Response) -> EventPage:
        result = await events.page(user.user_id, event_key)
        response.headers["Cache-Control"] = PRIVATE_NO_STORE
        return EventPage.model_validate(result)

    @router.get(
        "/me/events",
        response_model=MyEvents,
        summary="Архив событий игрока",
        description="Запуски, где у игрока есть прогресс; новые первыми, не более 50.",
        responses=problem_responses(401, 403),
    )
    async def my_events(user: User, response: Response) -> MyEvents:
        result = await events.mine(user.user_id)
        response.headers["Cache-Control"] = PRIVATE_NO_STORE
        return MyEvents.model_validate(result)

    @router.post(
        "/events/{event_key}/finds/{find}/claim",
        response_model=ClaimResult,
        summary="Засчитать находку-элемент интерфейса",
        description=(
            "Пустое тело, идемпотентно: повтор возвращает тот же итог с "
            "`newly_unlocked = false`. Ошибки: 404 `progression.event_not_found`, 404 "
            "`progression.event_find_not_found`, 409 `progression.event_not_active`, 409 "
            "`progression.event_find_not_ui_spot`, 409 `progression.event_find_locked` "
            "(финальная, пока не открыты остальные), 429 `progression.event_claim_rate_limited` "
            "(лимит на игрока, `Retry-After`), 503 (лимитер Redis недоступен, fail-closed)."
        ),
        responses=problem_responses(401, 403, 404, 409, 429, 503),
    )
    async def claim(
        event_key: KeyPath,
        find: FindPath,
        user: User,
        key: IdempotencyKey,
        response: Response,
    ) -> ClaimResult:
        result = await events.claim(user.user_id, event_key, find)
        response.headers["Cache-Control"] = PRIVATE_NO_STORE
        return ClaimResult.model_validate(result)
