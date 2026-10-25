"""Публичный API (/v1). На gateway: /api/catalog/v1/...."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from catalog_service.application.dto import Suggestion
from catalog_service.application.services import CatalogServices
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import MediaDomain
from gv_core.errors import RateLimitedError
from gv_service import problem_responses

# Жёсткий потолок на уровне HTTP; рабочий лимит длины (64) проверяет use-case
# с понятным кодом ``catalog.query_too_long``.
_Q_HARD_MAX = 256


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


class SuggestionOut(BaseModel):
    """Подсказка автокомплита."""

    id: uuid.UUID
    domain: MediaDomain
    title: str
    year: int | None
    subtitle: str | None

    @classmethod
    def of(cls, s: Suggestion) -> SuggestionOut:
        """Из DTO."""
        return cls(id=s.id, domain=s.domain, title=s.title, year=s.year, subtitle=s.subtitle)


class SuggestionList(BaseModel):
    """Список подсказок."""

    items: list[SuggestionOut]


class PersonOut(BaseModel):
    """Персона в карточке."""

    person_id: uuid.UUID
    name: str
    role: str
    order: int


class ItemCard(BaseModel):
    """Публичная карточка единицы (без ассетов и алиасов)."""

    id: uuid.UUID
    domain: MediaDomain
    kind: str
    title: str
    original_title: str | None
    year: int | None
    subtitle: str | None
    persons: list[PersonOut]
    genres: list[str]
    metrics: dict[str, float | int] = Field(default_factory=dict)

    @classmethod
    def of(cls, item: MediaItem) -> ItemCard:
        """Из агрегата."""
        snap = item.snapshot()
        return cls(
            id=item.id,
            domain=item.domain,
            kind=item.kind.value,
            title=item.title,
            original_title=item.original_title,
            year=item.year,
            subtitle=item.subtitle,
            persons=[PersonOut(**p) for p in snap["persons"]],
            genres=snap["genres"],
            metrics=snap["metrics"],
        )


class ItemCardList(BaseModel):
    """Пачка карточек (порядок не гарантирован)."""

    items: list[ItemCard]


def build_public_router(c: CatalogServices) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["catalog"])

    async def rate_limit(request: Request) -> None:
        limiter = c.rate_limiter
        if limiter is None:
            return
        # Сервис стоит за gateway: uvicorn с proxy_headers подставляет адрес клиента.
        client = request.client.host if request.client else "unknown"
        if not await limiter.hit(f"ac:{client}"):
            raise RateLimitedError("слишком много запросов", code="rate.limited")

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=c.service_name, status="ok")

    @router.get(
        "/autocomplete",
        response_model=SuggestionList,
        summary="Автокомплит названий (только активные единицы)",
        dependencies=[Depends(rate_limit)],
        responses=problem_responses(422, 429),
    )
    async def autocomplete(
        q: Annotated[str, Query(max_length=_Q_HARD_MAX, description="Префикс, ≤ 64 символов")],
        domain: MediaDomain | None = None,
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> SuggestionList:
        hits = await c.autocomplete(q, domain, limit)
        return SuggestionList(items=[SuggestionOut.of(s) for s in hits])

    @router.get(
        "/search",
        response_model=SuggestionList,
        summary="Поиск по названиям и алиасам",
        dependencies=[Depends(rate_limit)],
        responses=problem_responses(422, 429),
    )
    async def search(
        q: Annotated[str, Query(max_length=_Q_HARD_MAX)],
        domain: MediaDomain | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> SuggestionList:
        hits = await c.search(q, domain, limit)
        return SuggestionList(items=[SuggestionOut.of(s) for s in hits])

    @router.get(
        "/items",
        response_model=ItemCardList,
        summary="Карточки единиц пачкой",
        description=(
            "`ids` повторяется в запросе; дубли схлопываются, неизвестные и заблокированные "
            "пропускаются, порядок не гарантирован. Больше `items_batch_max` уникальных id - "
            "422 `catalog.batch_too_large`."
        ),
        responses=problem_responses(422),
    )
    async def get_items(
        ids: Annotated[list[uuid.UUID], Query(min_length=1, max_length=1000)],
    ) -> ItemCardList:
        return ItemCardList(items=[ItemCard.of(i) for i in await c.get_item.public_many(ids)])

    @router.get(
        "/items/{item_id}",
        response_model=ItemCard,
        summary="Карточка единицы",
        responses=problem_responses(404, 422),
    )
    async def get_item(item_id: uuid.UUID) -> ItemCard:
        return ItemCard.of(await c.get_item.public(item_id))

    return router
