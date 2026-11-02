"""Публичный API (/v1). На gateway: /api/collections/v1/....

Правила (план этапа 3b §2.5, §12):

* мутации — только пользователь (``require_user``: гость — 403 ``auth.guest_forbidden``),
  ``Idempotency-Key``; cookie-аутентификация — с Origin и CSRF (``gv_auth``);
* чужой или удалённый сборник, чужой черновик — 404 ``collections.not_found`` (не 403);
* ограниченный автор или санкция ``CUSTOM_CONTENT`` — 403 ``collections.author_restricted``;
* каталог без ``q`` — ``Cache-Control: public, max-age=30``, остальное — ``no-store``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from collections_service.api.common import (
    PUBLIC_CATALOG,
    ApiServices,
    NoStoreRoute,
    actor_of,
    client_subject,
    viewer_of,
)
from collections_service.api.schemas import (
    CollectionDetail,
    CollectionModeKey,
    CollectionPage,
    CollectionsMeta,
    CollectionStatus,
    CoverPresetOut,
    CreateCollectionRequest,
    Cursor,
    Difficulty,
    Domain,
    DraftOut,
    FavoriteOut,
    LikeOut,
    MetaLimits,
    MyCollectionsPage,
    PublishRequest,
    SaveDraftRequest,
    Sort,
    TagOut,
)
from collections_service.application.queries.collections import CatalogParams
from collections_service.domain.model import DraftBody
from gv_auth import Principal, optional_principal, require_user
from gv_service import IdempotencyKey, problem_responses

User = Annotated[Principal, Depends(require_user)]
OptionalSubject = Annotated[Principal | None, Depends(optional_principal)]
CollectionId = Annotated[UUID, Path(description="Идентификатор сборника")]
Limit = Annotated[int, Query(ge=1, le=50)]

_MUTATE = problem_responses(401, 403, 404, 409, 422, 429, 503)
_READ_ONE = problem_responses(401, 404, 422)


def build_public_router(c: ApiServices) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["collections"], route_class=NoStoreRoute)

    @router.get(
        "/meta",
        response_model=CollectionsMeta,
        response_model_exclude_none=True,
        summary="Сведения о сервисе и словари конструктора",
        description="Лимиты (`GV_COLLECTIONS__LIMITS__*`), пресеты обложек, словарь тегов.",
    )
    async def meta() -> CollectionsMeta:
        lim, dictionaries = c.limits, c.queries.meta
        return CollectionsMeta(
            service=c.service_name,
            status="ok",
            limits=MetaLimits(
                title_max=lim.title_max,
                description_max=lim.description_max,
                items_min=lim.items_min,
                items_max=lim.items_max,
                tags_max=lim.tags_max,
                emoji_max_graphemes=lim.emoji_max_graphemes,
                emoji_min_graphemes=lim.emoji_min_graphemes,
                drafts_max=lim.drafts_max,
                publish_per_day=lim.publish_per_day,
            ),
            cover_presets=[
                CoverPresetOut(id=p.id, gradient=list(p.gradient), emoji=p.emoji)
                for p in dictionaries.cover_presets
            ],
            tags=[
                TagOut.model_validate(
                    {"key": t.key, "title_key": t.title_key, "domains": [str(d) for d in t.domains]}
                )
                for t in dictionaries.tags
            ],
        )

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
        data = await c.author.create(actor_of(user, key), domain=body.domain, title=body.title)
        return DraftOut.model_validate(data)

    @router.get(
        "/me/collections",
        response_model=MyCollectionsPage,
        summary="Мои сборники",
        responses=problem_responses(401, 403, 422),
    )
    async def my_collections(
        user: User,
        status_: Annotated[CollectionStatus | None, Query(alias="status")] = None,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> MyCollectionsPage:
        data = await c.queries.mine(user.user_id, status=status_, cursor=cursor, limit=limit)
        return MyCollectionsPage.model_validate(data)

    @router.get(
        "/collections/{collection_id}/draft",
        response_model=DraftOut,
        summary="Черновик сборника",
        description="Нет черновика — тело опубликованной версии и `revision = 0`.",
        responses=problem_responses(401, 403, 404, 422),
    )
    async def get_draft(collection_id: CollectionId, user: User) -> DraftOut:
        data = await c.queries.draft(user.user_id, collection_id)
        return DraftOut.model_validate(data)

    @router.put(
        "/collections/{collection_id}/draft",
        response_model=DraftOut,
        summary="Сохранить черновик",
        description=(
            "Оптимистичная блокировка (D2): `base_revision` ≠ текущей — 409 "
            "`collections.draft_conflict`, `details.current` — актуальный `DraftOut` "
            "(`revision`, `updated_at`, `client_label`, `body`). 409 `collections.hidden` — "
            "сборник скрыт модератором; 409 `collections.deleted` — удалён; 422 "
            "`collections.draft_invalid` (`details.errors` — `[{path, code}]`: "
            "`collections.items_too_many`, `collections.tags_too_many`)."
        ),
        responses=_MUTATE,
    )
    async def save_draft(
        collection_id: CollectionId, body: SaveDraftRequest, user: User, key: IdempotencyKey
    ) -> DraftOut:
        data = await c.author.save_draft(
            actor_of(user, key),
            collection_id,
            base_revision=body.base_revision,
            client_label=body.client_label,
            body=DraftBody.from_json(body.body.model_dump(mode="json")),
        )
        return DraftOut.model_validate(data)

    @router.post(
        "/collections/{collection_id}/publish",
        response_model=CollectionDetail,
        summary="Опубликовать черновик",
        description=(
            "Новая `content_version` и событие `collections.CollectionPublished`. Ошибки: "
            "409 `collections.draft_conflict` (ревизия устарела, `details.current`), 409 "
            "`collections.hidden`, 409 `collections.deleted`, 422 "
            "`collections.publish_invalid` (`details.errors` — `[{path, code, message, reason?, "
            "min?, max?, actual?}]`: `code` — стабильный машинный код, `message` — готовый "
            "русский текст для показа («Нужно минимум 5 единиц, сейчас 3»), `path` — поле "
            "(`items`, `items[2]`, `title`); заголовок ответа перечисляет первые причины; "
            "`collections.items_too_few`, `collections.items_too_many`, "
            "`collections.item_duplicate`, `collections.item_domain_mismatch`, "
            "`collections.item_unavailable`, `collections.mode_invalid`, "
            "`collections.emoji_invalid`, `collections.emoji_too_short` (`min`), "
            "`collections.tag_unknown`, "
            "`collections.tags_too_many`, `collections.cover_unknown`, "
            "`collections.title_invalid`, `collections.description_invalid`; `reason` — код "
            "`ugc.*`), 429 `collections.publish_limit` (`details.retry_after`), 503 "
            "`collections.limiter_unavailable` (лимитер недоступен, fail-closed)."
        ),
        responses=_MUTATE,
    )
    async def publish(
        collection_id: CollectionId, body: PublishRequest, user: User, key: IdempotencyKey
    ) -> CollectionDetail:
        data = await c.author.publish(
            actor_of(user, key), collection_id, draft_revision=body.draft_revision
        )
        return CollectionDetail.model_validate(data)

    @router.delete(
        "/collections/{collection_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Удалить сборник",
        description="Повтор — 204. Опубликованный — событие `collections.CollectionDeleted`.",
        responses=_MUTATE,
    )
    async def delete_collection(
        collection_id: CollectionId, user: User, key: IdempotencyKey
    ) -> None:
        await c.author.delete(actor_of(user, key), collection_id)

    # ------------------------------------------------------------------ каталог
    @router.get(
        "/collections",
        response_model=CollectionPage,
        summary="Каталог сборников",
        description=(
            "Только опубликованные публичные (не скрытые) сборники. Поиск — по названию, "
            "описанию и словам тегов, не по единицам (D6). Сортировки: `popular` (лайки и "
            "завершения за окно), `new`, `likes`, `difficulty` (сложные раньше, без "
            "сложности — в конце), `featured` (только «рекомендуемые» по порядку). Без `q` — "
            "`Cache-Control: public, max-age=30`. 429 `collections.rate_limited` — лимит "
            "поиска с `q`."
        ),
        responses=problem_responses(422, 429),
    )
    async def catalog(
        request: Request,
        response: Response,
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
        params = CatalogParams(
            q=q,
            domain=domain,
            mode=mode,
            difficulty=difficulty,
            curated=curated,
            sort=sort,
            cursor=cursor,
            limit=limit,
        )
        data = await c.queries.catalog(params, subject=client_subject(request))
        if q is None:
            response.headers["Cache-Control"] = PUBLIC_CATALOG
        return CollectionPage.model_validate(data)

    @router.get(
        "/collections/{collection_id}",
        response_model=CollectionDetail,
        summary="Сборник",
        description=(
            "Публичный и unlisted (по ссылке). `items` — только автору, админу и игроку "
            "со статусом «пройден» текущей версии (D6), иначе `items: null`, "
            "`items_visible: false`; свои эмодзи единиц (`custom`) — только автору и админу. "
            "Скрытый — только автору и админу (`status: hidden`), остальным 404; гостю и "
            "анониму `me: null`."
        ),
        responses=_READ_ONE,
    )
    async def get_collection(
        collection_id: CollectionId, subject: OptionalSubject
    ) -> CollectionDetail:
        data = await c.queries.detail(collection_id, viewer_of(subject))
        return CollectionDetail.model_validate(data)

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
        return LikeOut.model_validate(await c.reactions.like(actor_of(user, key), collection_id))

    @router.delete(
        "/collections/{collection_id}/like",
        response_model=LikeOut,
        summary="Снять лайк",
        description="Идемпотентно, событие не публикуется (D13).",
        responses=_MUTATE,
    )
    async def unlike(collection_id: CollectionId, user: User, key: IdempotencyKey) -> LikeOut:
        return LikeOut.model_validate(await c.reactions.unlike(actor_of(user, key), collection_id))

    @router.put(
        "/collections/{collection_id}/favorite",
        response_model=FavoriteOut,
        summary="Добавить в избранное",
        responses=_MUTATE,
    )
    async def favorite(collection_id: CollectionId, user: User, key: IdempotencyKey) -> FavoriteOut:
        data = await c.reactions.favorite(actor_of(user, key), collection_id)
        return FavoriteOut.model_validate(data)

    @router.delete(
        "/collections/{collection_id}/favorite",
        response_model=FavoriteOut,
        summary="Убрать из избранного",
        responses=_MUTATE,
    )
    async def unfavorite(
        collection_id: CollectionId, user: User, key: IdempotencyKey
    ) -> FavoriteOut:
        data = await c.reactions.unfavorite(actor_of(user, key), collection_id)
        return FavoriteOut.model_validate(data)

    @router.get(
        "/me/favorites",
        response_model=CollectionPage,
        summary="Избранные сборники",
        description="Опубликованные сборники по времени добавления (новые раньше).",
        responses=problem_responses(401, 403, 422),
    )
    async def my_favorites(
        user: User, cursor: Annotated[Cursor | None, Query()] = None, limit: Limit = 20
    ) -> CollectionPage:
        data = await c.queries.favorites(user.user_id, cursor=cursor, limit=limit)
        return CollectionPage.model_validate(data)

    return router
