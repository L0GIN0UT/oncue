"""Публичный API (/v1). На gateway: /api/web/v1/....

Только GET (D4): мутаций нет, поэтому CSRF и идемпотентность не нужны. Токен берётся из
``Authorization: Bearer`` или cookie ``gv_access``, проверяется локально (JWKS identity) и
уходит источникам как Bearer. Публичные ответы — ``public, max-age, stale-while-revalidate``,
ответы с токеном — ``private, no-store``; ``Vary`` не даёт общему кэшу отдать анонимную
страницу вошедшему.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel

from bff_web_service.api.deps import BffDeps
from bff_web_service.api.public.schemas import (
    CollectionPage,
    CollectionSort,
    CollectionsPage,
    Difficulty,
    Domain,
    HomePage,
    LeaderboardsPage,
    LibraryPage,
    LibraryShelfPage,
    MatchPage,
    OnlinePage,
    ResultsPage,
    SessionInfo,
    SoloPage,
)
from bff_web_service.application.dto import ANONYMOUS, CatalogQuery, SubjectKind, Viewer
from bff_web_service.application.ports import TRACE_HEADERS
from bff_web_service.settings import HttpCacheSettings
from gv_auth import JwtVerifier, extract_token
from gv_core.errors import DependencyUnavailableError, UnauthenticatedError
from gv_service import problem_responses

log = structlog.get_logger(__name__)

PRIVATE = "private, no-store"
_SHELF_REF = (
    r"^(system:[a-z][a-z0-9_]{0,63}|"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_CURSOR = r"^[A-Za-z0-9_-]{1,512}$"
# Ключ режима без перечня доменов и типов: допустимость проверяет collections (422).
_MODE_KEY = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){1,4}$"
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
        return REJECTED
    except DependencyUnavailableError:
        log.warning("bff.jwks_unavailable")
        return Viewer(token=token)
    kind = SubjectKind.GUEST if principal.is_guest else SubjectKind.USER
    return Viewer(token=token, kind=kind, verified=True)


REJECTED = Viewer(token_rejected=True)
"""Токен не прошёл проверку: для экранов — аноним (токен дальше не уходит)."""

CurrentViewer = Annotated[Viewer, Depends(resolve_viewer)]


def _cache_headers(
    response: Response,
    *,
    personal: bool,
    policy: HttpCacheSettings,
    max_age_s: int | None = None,
) -> None:
    response.headers["Vary"] = VARY
    if personal:
        response.headers["Cache-Control"] = PRIVATE
        return
    response.headers["Cache-Control"] = (
        f"public, max-age={policy.public_max_age_s if max_age_s is None else max_age_s}, "
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
        description=(
            "Блоки: дейлики и недельная сетка субъекта, витрина режимов по группам "
            "(`source_type`, `presentation`), личные рекорды (только пользователю; гостю и "
            "анониму — `records: null`). Ответ с токеном — `private, no-store`."
        ),
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

    # ------------------------------------------------------------------ этап 3b
    @router.get(
        "/pages/collections",
        response_model=CollectionsPage,
        summary="Каталог сборников",
        description=(
            "Без `q` и `cursor` — витрины `featured`, `popular`, `new` с общими фильтрами "
            "(`results: null`; публично, `public, max-age`). С `q` или `cursor` — только "
            "`results` в сортировке `sort` (витрины `null`); с `q` — `private, no-store`. "
            "Поиск — по названию, описанию и тегам. Упавший collections — блоки "
            "`unavailable`, страница 200; курсор не от этой сортировки — 422 от collections."
        ),
        responses=problem_responses(422),
    )
    async def collections_page(
        *,
        viewer: CurrentViewer,  # noqa: ARG001 — каталог одинаков для всех; нужна трасса
        response: Response,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        domain: Annotated[Domain | None, Query()] = None,
        mode: Annotated[str | None, Query(max_length=100, pattern=_MODE_KEY)] = None,
        difficulty: Annotated[Difficulty | None, Query()] = None,
        curated: Annotated[bool | None, Query()] = None,
        sort: Annotated[CollectionSort, Query()] = "popular",
        cursor: Annotated[str | None, Query(pattern=_CURSOR)] = None,
    ) -> CollectionsPage:
        page = await deps.get_collections(
            CatalogQuery(
                q=q,
                domain=domain,
                mode=mode,
                difficulty=difficulty,
                curated=curated,
                sort=sort,
                cursor=cursor,
            )
        )
        _cache_headers(response, personal=not page.cacheable, policy=policy)
        return CollectionsPage.model_validate(page, from_attributes=True)

    @router.get(
        "/pages/collections/{collection_id}",
        response_model=CollectionPage,
        summary="Страница сборника",
        description=(
            "Карточка (collections; `items` — только автору, админу и прошедшему, D6), "
            "режимы с играбельностью (game; недоступен — `modes: unavailable`, карточка "
            "отдаётся), `me` — только пользователю (гостю и анониму — null). Нет, скрыт "
            "для чужих или удалён — 404 `collections.not_found`. С токеном — "
            "`private, no-store`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def collection_page(
        viewer: CurrentViewer,
        response: Response,
        collection_id: Annotated[UUID, Path()],
    ) -> CollectionPage:
        page = await deps.get_collection(viewer, collection_id)
        _cache_headers(response, personal=page.personal, policy=policy)
        return CollectionPage.model_validate(page, from_attributes=True)

    @router.get(
        "/pages/library",
        response_model=LibraryPage,
        summary="Медиатека: полки",
        description=(
            "Только пользователь: без токена — 401, гость — 403 `auth.guest_forbidden`. "
            "Упавший library — `shelves: unavailable`, страница 200. Всегда "
            "`private, no-store`."
        ),
        responses=problem_responses(401, 403),
    )
    async def library_page(viewer: CurrentViewer, response: Response) -> LibraryPage:
        _cache_headers(response, personal=True, policy=policy)
        page = await deps.get_library(viewer)
        return LibraryPage.model_validate(page, from_attributes=True)

    @router.get(
        "/pages/library/{shelf_ref}",
        response_model=LibraryShelfPage,
        summary="Медиатека: полка",
        description=(
            "`shelf_ref` — `system:<key>` или uuid своей полки; чужая и несуществующая — "
            "404 `library.shelf_not_found`; без токена — 401, гость — 403. Упавший "
            "library — `shelf: unavailable`. Всегда `private, no-store`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def library_shelf_page(
        viewer: CurrentViewer,
        response: Response,
        shelf_ref: Annotated[str, Path(pattern=_SHELF_REF)],
        cursor: Annotated[str | None, Query(pattern=_CURSOR)] = None,
    ) -> LibraryShelfPage:
        _cache_headers(response, personal=True, policy=policy)
        page = await deps.get_library_shelf(viewer, shelf_ref, cursor=cursor)
        return LibraryShelfPage.model_validate(page, from_attributes=True)

    # ------------------------------------------------------------------ этап 4a
    @router.get(
        "/pages/online",
        response_model=OnlinePage,
        summary="Хаб онлайна",
        description=(
            "Блоки: открытые лобби и мои последние матчи (match), онлайн-режимы для создания "
            "комнаты (game), очереди рейтингового подбора (match), мои рейтинги, действующие "
            "сезоны и топ недели (rating). Каждый блок деградирует независимо (`unavailable`, "
            "страница 200). "
            "Онлайн только с аккаунтом (D13): гостю и анониму — `status: auth_required`, "
            "`blocks: null`, match не вызывается. Токен не прошёл проверку или match ответил "
            "401 — `status: session_expired` (обновить сессию и повторить). Всегда "
            "`private, no-store`."
        ),
    )
    async def online_page(viewer: CurrentViewer, response: Response) -> OnlinePage:
        _cache_headers(response, personal=True, policy=policy)
        page = await deps.get_online(viewer)
        return OnlinePage.model_validate(page, from_attributes=True)

    # ------------------------------------------------------------------ этап 4b
    @router.get(
        "/pages/leaderboards",
        response_model=LeaderboardsPage,
        summary="Лидерборды",
        description=(
            "Публичная страница: ladder-ы с сезоном и званиями, лидерборд (`ladder`, `cursor`; "
            "без `ladder` — первый из списка) и топ недели (rating). Блоки деградируют "
            "независимо. Без `around` ответ общий: `public, max-age=10`. `around=me` — окно "
            "вокруг пользователя и строки `me`: нужен вход (иначе 401), ответ "
            "`private, no-store`; в калибровке — обычная первая страница. Нет ladder-а — 404 "
            "`rating.ladder_not_found`, чужой курсор — 422."
        ),
        responses=problem_responses(401, 404, 422),
    )
    async def leaderboards_page(
        viewer: CurrentViewer,
        response: Response,
        ladder: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)] = None,
        cursor: Annotated[str | None, Query(pattern=_CURSOR)] = None,
        around: Annotated[Literal["me"] | None, Query()] = None,
    ) -> LeaderboardsPage:
        page = await deps.get_leaderboards(viewer, ladder, cursor, around_me=around == "me")
        _cache_headers(
            response, personal=page.personal, policy=policy, max_age_s=policy.leaderboards_max_age_s
        )
        return LeaderboardsPage.model_validate(page, from_attributes=True)

    @router.get(
        "/pages/match/{match_id}",
        response_model=MatchPage,
        summary="Итог матча",
        description=(
            "Итог (match) + карточки единиц раундов (catalog) + флаги «в медиатеке» (library). "
            "Только пользователь: без токена — 401, гость — 403 `auth.guest_forbidden`. "
            "Нет или ещё идёт — 404 `match.match_not_found`. Упавший match — все блоки "
            "`unavailable`; catalog или library — только их блок. Всегда `private, no-store`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def match_page(
        viewer: CurrentViewer, response: Response, match_id: Annotated[UUID, Path()]
    ) -> MatchPage:
        _cache_headers(response, personal=True, policy=policy)
        page = await deps.get_match(viewer, match_id)
        return MatchPage.model_validate(page, from_attributes=True)

    return router
