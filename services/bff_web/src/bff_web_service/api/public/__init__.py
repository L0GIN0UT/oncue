"""Публичный API (/v1). На gateway: /api/web/v1/....

Только GET (D4): мутаций нет, поэтому CSRF и идемпотентность не нужны. Токен берётся из
``Authorization: Bearer`` или cookie ``gv_access``, проверяется локально (JWKS identity) и
уходит источникам как Bearer. Публичные ответы — ``public, max-age, stale-while-revalidate``,
ответы с токеном — ``private, no-store``; ``Vary`` не даёт общему кэшу отдать анонимную
страницу вошедшему.
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel

from bff_web_service.api.deps import BffDeps
from bff_web_service.api.public.schemas import (
    Domain,
    HomePage,
    ResultsPage,
    SessionInfo,
    SoloPage,
)
from bff_web_service.application.dto import ANONYMOUS, SubjectKind, Viewer
from bff_web_service.application.ports import TRACE_HEADERS
from bff_web_service.settings import HttpCacheSettings
from gv_auth import JwtVerifier, extract_token
from gv_core.errors import DependencyUnavailableError, UnauthenticatedError
from gv_service import problem_responses

log = structlog.get_logger(__name__)

PRIVATE = "private, no-store"
VARY = "Authorization, Cookie"
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")
_TRACESTATE_MAX = 512


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def _trace_headers(request: Request) -> dict[str, str]:
    """``traceparent``/``tracestate`` входящего запроса — только корректного формата (W3C)."""
    headers: dict[str, str] = {}
    parent = request.headers.get("traceparent", "")
    if _TRACEPARENT.fullmatch(parent):
        headers["traceparent"] = parent
        state = request.headers.get("tracestate", "")
        if state and len(state) <= _TRACESTATE_MAX and state.isascii() and state.isprintable():
            headers["tracestate"] = state
    return headers


async def resolve_viewer(request: Request) -> Viewer:
    """Кто смотрит страницу.

    * Нет токена или он не прошёл проверку (подделан, истёк, отозван) — аноним: экран не
      падает, фронтенд обновит сессию сам (bff refresh не делает, план §11).
    * Ключи identity недоступны — токен уходит источникам непроверенным: они проверят его
      сами, а вид субъекта определит ``GetSession`` по ответу identity.

    Заодно запоминает контекст трассы для исходящих вызовов.
    """
    TRACE_HEADERS.set(_trace_headers(request))
    found = extract_token(request)
    if found is None:
        return ANONYMOUS
    token, _source = found  # cookie на GET без мутаций — CSRF не нужен (D4)
    verifier = getattr(request.app.state, "jwt_verifier", None)
    if not isinstance(verifier, JwtVerifier):
        return Viewer(token=token)
    try:
        principal = await verifier.verify(token)
    except UnauthenticatedError as exc:
        log.info("bff.token_rejected", code=exc.code)
        return ANONYMOUS
    except DependencyUnavailableError:
        log.warning("bff.jwks_unavailable")
        return Viewer(token=token)
    kind = SubjectKind.GUEST if principal.is_guest else SubjectKind.USER
    return Viewer(token=token, kind=kind, verified=True)


CurrentViewer = Annotated[Viewer, Depends(resolve_viewer)]


def _cache_headers(response: Response, *, personal: bool, policy: HttpCacheSettings) -> None:
    response.headers["Vary"] = VARY
    if personal:
        response.headers["Cache-Control"] = PRIVATE
        return
    response.headers["Cache-Control"] = (
        f"public, max-age={policy.public_max_age_s}, "
        f"stale-while-revalidate={policy.stale_while_revalidate_s}"
    )


def build_public_router(deps: BffDeps) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["bff_web"])
    policy = deps.http_cache

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service="bff_web", status="active")

    @router.get(
        "/session",
        response_model=SessionInfo,
        summary="Кто смотрит страницу",
        description="Недействительный или истёкший токен — `subject: null`, а не ошибка.",
    )
    async def session(viewer: CurrentViewer, response: Response) -> SessionInfo:
        view = await deps.get_session(viewer)
        # Ответ зависит от cookie даже у анонима (cookie могла истечь): общий кэш недопустим.
        _cache_headers(response, personal=True, policy=policy)
        return SessionInfo.model_validate(view, from_attributes=True)

    @router.get("/pages/home", response_model=HomePage, summary="Главная: дейлики и режимы")
    async def home_page(viewer: CurrentViewer, response: Response) -> HomePage:
        page = await deps.get_home(viewer)
        _cache_headers(response, personal=page.personal, policy=policy)
        return HomePage.model_validate(page, from_attributes=True)

    @router.get(
        "/pages/solo",
        response_model=SoloPage,
        summary="Хаб соло-режимов",
        responses=problem_responses(422),
    )
    async def solo_page(
        viewer: CurrentViewer,
        response: Response,
        domain: Annotated[Domain | None, Query()] = None,
    ) -> SoloPage:
        page = await deps.get_solo(viewer, domain)
        _cache_headers(response, personal=page.personal, policy=policy)
        return SoloPage.model_validate(page, from_attributes=True)

    @router.get(
        "/pages/results/{session_id}",
        response_model=ResultsPage,
        summary="Экран результатов сессии",
        description=(
            "Только своя завершённая сессия. Чужая и несуществующая — одинаково 404; "
            "незавершённая — 409 (коды game)."
        ),
        responses=problem_responses(401, 403, 404, 409, 422),
    )
    async def results_page(
        viewer: CurrentViewer, response: Response, session_id: Annotated[UUID, Path()]
    ) -> ResultsPage:
        page = await deps.get_results(viewer, session_id)
        _cache_headers(response, personal=True, policy=policy)
        return ResultsPage.model_validate(page, from_attributes=True)

    return router
