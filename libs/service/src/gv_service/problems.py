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
from starlette.exceptions import HTTPException as StarletteHTTPException

from gv_core.context import current_context
from gv_core.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    FeatureDisabledError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
    UnauthenticatedError,
)

log = structlog.get_logger(__name__)

PROBLEM_JSON = "application/problem+json"

# Порядок важен: проверяется первый подходящий класс (наследники раньше предков).
_STATUS_BY_ERROR: tuple[tuple[type[AppError], int], ...] = (
    (UnauthenticatedError, 401),
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
    (InvalidInputError, 422),
    (DomainError, 422),
    (RateLimitedError, 429),
    (FeatureDisabledError, 403),
    (DependencyUnavailableError, 503),
)


def status_for(error: AppError) -> int:
    """HTTP-статус для ошибки приложения."""
    for cls, status in _STATUS_BY_ERROR:
        if isinstance(error, cls):
            return status
    return 400


def problem(
    status: int,
    code: str,
    title: str,
    *,
    detail: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> JSONResponse:
    """Собрать ответ Problem Details."""
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
    headers = {"Retry-After": "1"} if status in (429, 503) else None
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def register_problem_handlers(app: FastAPI) -> None:
    """Подключить обработчики ошибок к приложению."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        status = status_for(exc)
        if status >= 500:  # noqa: PLR2004
            log.warning("request.dependency_unavailable", code=exc.code, error=exc.message)
        return problem(
            status, exc.code, exc.message, extra={"details": exc.details} if exc.details else None
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
