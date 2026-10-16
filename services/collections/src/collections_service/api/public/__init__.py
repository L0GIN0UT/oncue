"""Публичный API (/v1). На gateway: /api/collections/v1/....

Шаг 0a этапа 3b: схемы и коды ошибок зафиксированы, ручки отвечают 501 до потока K
(docs/plans/stage-3b.md §2.5, §5). Правила:

* мутации — только пользователь (``require_user``: гость — 403 ``auth.guest_forbidden``),
  ``Idempotency-Key``; cookie-аутентификация — с Origin и CSRF (``gv_auth``);
* чужой или удалённый сборник, чужой черновик — 404 ``collections.not_found`` (не 403);
* ограниченный автор или санкция ``CUSTOM_CONTENT`` — 403 ``collections.author_restricted``.
"""

# ruff: noqa: ARG001 — заглушки этапа 3b (501) объявляют параметры контракта до реализации.

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status

from collections_service.api.schemas import (
    CollectionDetail,
    CollectionModeKey,
    CollectionPage,
    CollectionsMeta,
    CollectionStatus,
    CreateCollectionRequest,
    Cursor,
    Difficulty,
    Domain,
    DraftOut,
    FavoriteOut,
    LikeOut,
    MyCollectionsPage,
    PublishRequest,
    SaveDraftRequest,
    Sort,
)
from gv_auth import Principal, optional_principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, Infrastructure, problem_responses

User = Annotated[Principal, Depends(require_user)]
OptionalSubject = Annotated[Principal | None, Depends(optional_principal)]
CollectionId = Annotated[UUID, Path(description="Идентификатор сборника")]
Limit = Annotated[int, Query(ge=1, le=50)]
STAGE_3B = "этап 3b: реализуется в потоке K (docs/plans/stage-3b.md)"

_MUTATE = problem_responses(401, 403, 404, 409, 422, 429, 501)
_READ_ONE = problem_responses(401, 404, 422, 501)


def build_public_router(infra: Infrastructure) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["collections"])

    @router.get(
        "/meta",
        response_model=CollectionsMeta,
        response_model_exclude_none=True,
        summary="Сведения о сервисе и словари конструктора",
        description="Лимиты (`GV_COLLECTIONS__LIMITS__*`), пресеты обложек, словарь тегов.",
    )
    async def meta() -> CollectionsMeta:
        return CollectionsMeta(service=infra.settings.service_name, status="stub")

    # ------------------------------------------------------------------ черновики автора
    @router.post(
        "/collections",
        response_model=DraftOut,
        status_code=status.HTTP_201_CREATED,
        summary="Создать сборник (черновик)",
        description=(
            "Ошибки: 409 `collections.drafts_limit` (`details.max`), 422 "
            "`collections.title_invalid` (`details.reason` — код `ugc.*`), 403 "
            "`collections.author_restricted`."
        ),
        responses=_MUTATE,
    )
    async def create_collection(
        body: CreateCollectionRequest, user: User, key: IdempotencyKey
    ) -> DraftOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.get(
        "/me/collections",
        response_model=MyCollectionsPage,
        summary="Мои сборники",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def my_collections(
        user: User,
        status_: Annotated[CollectionStatus | None, Query(alias="status")] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> MyCollectionsPage:
        raise NotImplementedYetError(STAGE_3B)

    @router.get(
        "/collections/{collection_id}/draft",
        response_model=DraftOut,
        summary="Черновик сборника",
        description="Нет черновика — тело опубликованной версии и `revision = 0`.",
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def get_draft(collection_id: CollectionId, user: User) -> DraftOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.put(
        "/collections/{collection_id}/draft",
        response_model=DraftOut,
        summary="Сохранить черновик",
        description=(
            "Оптимистичная блокировка (D2): `base_revision` ≠ текущей — 409 "
            "`collections.draft_conflict`, `details.current` — актуальный `DraftOut` "
            "(`revision`, `updated_at`, `client_label`, `body`). 409 `collections.hidden` — "
            "сборник скрыт модератором; 422 `collections.draft_invalid` (`details.errors` — "
            "`[{path, code}]`)."
        ),
        responses=_MUTATE,
    )
    async def save_draft(
        collection_id: CollectionId, body: SaveDraftRequest, user: User, key: IdempotencyKey
    ) -> DraftOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.post(
        "/collections/{collection_id}/publish",
        response_model=CollectionDetail,
        summary="Опубликовать черновик",
        description=(
            "Новая `content_version` и событие `collections.CollectionPublished`. Ошибки: "
            "409 `collections.draft_conflict` (ревизия устарела), 409 `collections.hidden`, "
            "409 `collections.deleted`, 422 `collections.publish_invalid` (`details.errors` — "
            "`[{path, code}]`: `collections.items_too_few`, `collections.items_too_many`, "
            "`collections.item_duplicate`, `collections.item_domain_mismatch`, "
            "`collections.item_unavailable`, `collections.mode_invalid`, "
            "`collections.emoji_invalid`, `collections.tag_unknown`, "
            "`collections.cover_unknown`, `collections.title_invalid`, "
            "`collections.description_invalid`), 429 `collections.publish_limit` "
            "(fail-closed)."
        ),
        responses=_MUTATE,
    )
    async def publish(
        collection_id: CollectionId, body: PublishRequest, user: User, key: IdempotencyKey
    ) -> CollectionDetail:
        raise NotImplementedYetError(STAGE_3B)

    @router.delete(
        "/collections/{collection_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Удалить сборник",
        description="Опубликованный — событие `collections.CollectionDeleted`.",
        responses=_MUTATE,
    )
    async def delete_collection(
        collection_id: CollectionId, user: User, key: IdempotencyKey
    ) -> None:
        raise NotImplementedYetError(STAGE_3B)

    # ------------------------------------------------------------------ каталог
    @router.get(
        "/collections",
        response_model=CollectionPage,
        summary="Каталог сборников",
        description=(
            "Только опубликованные публичные (не скрытые) сборники. Поиск — по названию, "
            "описанию и тегам, не по единицам (D6). Без `q` — `Cache-Control: public, "
            "max-age=30`."
        ),
        responses=problem_responses(422, 429, 501),
    )
    async def catalog(
        *,
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        domain: Annotated[Domain | None, Query()] = None,
        mode: Annotated[CollectionModeKey | None, Query()] = None,
        difficulty: Annotated[Difficulty | None, Query()] = None,
        curated: Annotated[bool | None, Query()] = None,
        sort: Annotated[Sort, Query()] = "popular",
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> CollectionPage:
        raise NotImplementedYetError(STAGE_3B)

    @router.get(
        "/collections/{collection_id}",
        response_model=CollectionDetail,
        summary="Сборник",
        description=(
            "Публичный и unlisted (по ссылке). `items` — только автору, админу и игроку "
            "со статусом «пройден» (D6), иначе `items: null`, `items_visible: false`. "
            "Скрытый — только автору (`status: hidden`), остальным 404."
        ),
        responses=_READ_ONE,
    )
    async def get_collection(
        collection_id: CollectionId, subject: OptionalSubject
    ) -> CollectionDetail:
        raise NotImplementedYetError(STAGE_3B)

    # ------------------------------------------------------------------ реакции
    @router.put(
        "/collections/{collection_id}/like",
        response_model=LikeOut,
        summary="Лайкнуть сборник",
        description=(
            "Идемпотентно. Новый лайк — событие `collections.CollectionLiked`. 403 "
            "`collections.own_collection` — свой сборник; 429 `collections.rate_limited`."
        ),
        responses=_MUTATE,
    )
    async def like(collection_id: CollectionId, user: User, key: IdempotencyKey) -> LikeOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.delete(
        "/collections/{collection_id}/like",
        response_model=LikeOut,
        summary="Снять лайк",
        description="Идемпотентно, событие не публикуется (D13).",
        responses=_MUTATE,
    )
    async def unlike(collection_id: CollectionId, user: User, key: IdempotencyKey) -> LikeOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.put(
        "/collections/{collection_id}/favorite",
        response_model=FavoriteOut,
        summary="Добавить в избранное",
        responses=_MUTATE,
    )
    async def favorite(collection_id: CollectionId, user: User, key: IdempotencyKey) -> FavoriteOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.delete(
        "/collections/{collection_id}/favorite",
        response_model=FavoriteOut,
        summary="Убрать из избранного",
        responses=_MUTATE,
    )
    async def unfavorite(
        collection_id: CollectionId, user: User, key: IdempotencyKey
    ) -> FavoriteOut:
        raise NotImplementedYetError(STAGE_3B)

    @router.get(
        "/me/favorites",
        response_model=CollectionPage,
        summary="Избранные сборники",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def my_favorites(
        user: User, cursor: Annotated[Cursor | None, Query()] = None, limit: Limit = 20
    ) -> CollectionPage:
        raise NotImplementedYetError(STAGE_3B)

    return router
