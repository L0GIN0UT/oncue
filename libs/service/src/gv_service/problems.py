"""Ошибки API в формате RFC 9457 (Problem Details).

Единственное место, где ``AppError`` превращается в HTTP-ответ. Неожиданные исключения
отдаются как 500 без деталей (детали — только в логе с correlation_id), чтобы не раскрывать
внутреннее устройство.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from gv_core.context import current_context
from gv_core.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    FeatureDisabledError,
    ForbiddenError,
    GoneError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnauthenticatedError,
)

log = structlog.get_logger(__name__)

PROBLEM_JSON = "application/problem+json"

# Статусы, для которых клиенту сообщаем, через сколько повторить (RFC 9110 §10.2.3).
_RETRYABLE = frozenset({429, 503})
_DEFAULT_RETRY_AFTER = 1

# Порядок важен: проверяется первый подходящий класс (наследники раньше предков).
_STATUS_BY_ERROR: tuple[tuple[type[AppError], int], ...] = (
    (UnauthenticatedError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (GoneError, 410),
    (ConflictError, 409),
    (InvalidInputError, 422),
    (DomainError, 422),
    (RateLimitedError, 429),
    (FeatureDisabledError, 403),
    (DependencyUnavailableError, 503),
)


class Problem(BaseModel):
    """RFC 9457 Problem Details — тело любого ответа-ошибки сервиса (для OpenAPI).

    Attributes:
        type: URN проблемы ``urn:guessverse:problem:<code>``.
        title: Человекочитаемый заголовок.
        status: HTTP-статус.
        code: Машинный код ошибки — на него опираются клиенты.
        detail: Пояснение.
        correlation_id: Идентификатор цепочки запроса (для поиска в логах).
        details: Безопасные для клиента подробности (``AppError.details``).
        errors: Ошибки валидации полей запроса.
    """

    type: str
    title: str
    status: int
    code: str
    detail: str | None = None
    correlation_id: str | None = None
    details: dict[str, Any] | None = None
    errors: list[dict[str, Any]] | None = Field(
        default=None, description="Ошибки валидации: loc, msg, type"
    )


_STATUS_TITLES: dict[int, str] = {
    400: "Некорректный запрос",
    401: "Не аутентифицирован",
    403: "Недостаточно прав",
    404: "Не найдено",
    409: "Конфликт состояния",
    410: "Ресурс больше недоступен",
    422: "Некорректные данные",
    429: "Слишком много запросов",
    503: "Зависимость временно недоступна",
}


def problem_responses(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Описание ответов-ошибок для ``responses=`` роутера FastAPI.

    Схема ``Problem`` регистрируется в ``components`` через ``model``, а реальный тип
    содержимого ответов — ``application/problem+json``.

    Example:
        ``@router.post("/x", responses=problem_responses(404, 409, 422))``
    """
    ref = {"$ref": "#/components/schemas/Problem"}
    return {
        status: {
            "model": Problem,
            "description": _STATUS_TITLES.get(status, "Problem Details"),
            "content": {PROBLEM_JSON: {"schema": ref}},
        }
        for status in statuses
    }


def status_for(error: AppError) -> int:
    """HTTP-статус для ошибки приложения."""
    for cls, status in _STATUS_BY_ERROR:
        if isinstance(error, cls):
            return status
    return 400


def retry_after_seconds(details: Mapping[str, Any]) -> int:
    """Секунды для ``Retry-After`` из ``details.retry_after`` ошибки.

    Принимается только целое ``>= 1``; всё остальное (нет ключа, 0, отрицательное,
    дробное, строка) — 1 секунда: заголовок не должен ни ломаться, ни звать к повтору
    «немедленно».
    """
    value = details.get("retry_after")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return _DEFAULT_RETRY_AFTER


def problem(
    status: int,
    code: str,
    title: str,
    *,
    detail: str | None = None,
    extra: Mapping[str, Any] | None = None,
    retry_after: int = _DEFAULT_RETRY_AFTER,
) -> JSONResponse:
    """Собрать ответ Problem Details.

    Args:
        status: HTTP-статус.
        code: Машинный код ошибки.
        title: Человекочитаемый заголовок.
        detail: Пояснение (необязательно).
        extra: Дополнительные поля тела (``details``, ``errors``).
        retry_after: Секунды для заголовка ``Retry-After``; ставится только для 429/503.
    """
    body: dict[str, Any] = {
        "type": f"urn:guessverse:problem:{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    ctx = current_context()
    if ctx is not None:
        body["correlation_id"] = str(ctx.correlation_id)
    body.update(extra or {})
    headers = {"Retry-After": str(max(retry_after, 1))} if status in _RETRYABLE else None
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def register_problem_handlers(app: FastAPI) -> None:
    """Подключить обработчики ошибок к приложению."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        status = status_for(exc)
        if status >= 500:  # noqa: PLR2004
            log.warning("request.dependency_unavailable", code=exc.code, error=exc.message)
        return problem(
            status,
            exc.code,
            exc.message,
            extra={"details": exc.details} if exc.details else None,
            retry_after=retry_after_seconds(exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return problem(
            422,
            "request.validation_failed",
            "Некорректные данные запроса",
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "route.not_found", 405: "route.method_not_allowed"}.get(
            exc.status_code, "http.error"
        )
        return problem(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("request.unhandled_error", error_type=type(exc).__name__)
        return problem(500, "internal.error", "Внутренняя ошибка сервиса")
