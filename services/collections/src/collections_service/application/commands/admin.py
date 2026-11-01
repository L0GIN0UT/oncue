"""Мутации admin-API (K9, D14, ADR 0030): кураторские сборники, скрытие, «рекомендуемое»,
ограничение автора.

Все — с ``Idempotency-Key`` (ключ в транзакции мутации) и ``reason``; каждая пишет строку
аудита ``admin_actions`` (без UGC-текста: версии, статусы, флаги).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog

from collections_service.application.common import AdminContext
from collections_service.application.ports import (
    AdminAction,
    CollectionsUnitOfWork,
    Json,
    Restriction,
    UnitOfWorkFactory,
)
from collections_service.application.views import Presenter, restriction_json
from collections_service.domain.collection import Collection
from collections_service.domain.errors import CollectionNotFoundError, NotUgcError
from collections_service.domain.model import Domain, DraftBody, Kind, Status
from collections_service.domain.policy import CollectionPolicy
from gv_core.clock import Clock
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent

log = structlog.get_logger(__name__)


async def admin_detail(
    uow: CollectionsUnitOfWork, presenter: Presenter, collection_id: UUID
) -> Json:
    """``AdminCollectionDetail``: опубликованный состав, а у черновика — состав черновика."""
    row = await uow.reads.row(collection_id)
    if row is None:
        raise CollectionNotFoundError()
    if row.content_version > 0:
        items = await uow.reads.items(collection_id)
    else:
        collection = await uow.collections.get(collection_id)
        draft = collection.state.draft if collection else None
        pairs = [(i.media_item_id, i.emoji) for i in draft.body.items] if draft else []
        items = await uow.projection.item_rows(pairs)
    restricted = row.author_id is not None and await uow.restrictions.get(row.author_id) is not None
    return presenter.admin_detail(
        row, restricted=restricted, items=presenter.items(items, with_custom=True)
    )


class AdminCommands:
    """Модерация и кураторство.

    Args:
        uow: Фабрика транзакций.
        clock: Часы.
        policy: Проверки содержимого (кураторские проверяются как UGC).
        presenter: JSON строк админки.
    """

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, policy: CollectionPolicy, presenter: Presenter
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy
        self._presenter = presenter

    async def _audit(
        self,
        uow: CollectionsUnitOfWork,
        ctx: AdminContext,
        action: str,
        target: object,
        **details: Any,
    ) -> None:
        await uow.audit.record(
            AdminAction(
                actor_id=ctx.actor_id,
                action=action,
                target=str(target),
                reason=ctx.reason,
                at=self._clock.now(),
                details=details,
            )
        )

    async def _mutate(
        self,
        ctx: AdminContext,
        action: str,
        target: object,
        body: dict[str, Any],
        perform: Callable[[CollectionsUnitOfWork], Awaitable[Json]],
    ) -> Json:
        fingerprint = request_fingerprint(action, target, {**body, "reason": ctx.reason})
        async with self._uow() as uow:

            async def run() -> Json:
                return await perform(uow)

            response, executed = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, run
            )
            await uow.commit()
        if executed:
            log.info("collections.admin_action", action=action, target=str(target))
        return response

    @staticmethod
    async def _existing(uow: CollectionsUnitOfWork, collection_id: UUID) -> Collection:
        collection = await uow.collections.get(collection_id, for_update=True)
        if collection is None or collection.state.status is Status.DELETED:
            raise CollectionNotFoundError()
        return collection

    # ------------------------------------------------------------------ кураторские
    async def create_curated(self, ctx: AdminContext, *, domain: str, body: DraftBody) -> Json:
        """Кураторский сборник, опубликованный сразу (``CollectionPublished``).

        Raises:
            PublishInvalidError: как у публикации UGC (422).
        """

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            info = await uow.projection.items_info([i.media_item_id for i in body.items])
            checked = self._policy.publishable(Domain(domain), body, info)
            collection = Collection.create_curated(
                uuid7(), domain=Domain(domain), body=checked, now=self._clock.now()
            )
            await uow.collections.add(collection)
            uow.track(collection)
            await self._audit(uow, ctx, "collection.curated_create", collection.id, version=1)
            return await admin_detail(uow, self._presenter, collection.id)

        return await self._mutate(
            ctx,
            "collection.curated_create",
            None,
            {"domain": domain, "body": body.to_json()},
            perform,
        )

    async def update_curated(
        self, ctx: AdminContext, collection_id: UUID, *, base_version: int, body: DraftBody
    ) -> Json:
        """Новая версия кураторского сборника.

        Raises:
            CollectionNotFoundError: нет или удалён (404).
            NotCuratedError: UGC (409); VersionConflictError: версия устарела (409).
            PublishInvalidError: 422.
        """

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            collection = await self._existing(uow, collection_id)
            info = await uow.projection.items_info([i.media_item_id for i in body.items])
            checked = self._policy.publishable(collection.state.domain, body, info)
            collection.update_curated(checked, base_version=base_version, now=self._clock.now())
            await uow.collections.save(collection)
            uow.track(collection)
            await self._audit(
                uow,
                ctx,
                "collection.curated_update",
                collection_id,
                version=collection.state.content_version,
            )
            return await admin_detail(uow, self._presenter, collection_id)

        return await self._mutate(
            ctx,
            "collection.curated_update",
            collection_id,
            {"base": base_version, "body": body.to_json()},
            perform,
        )

    # ------------------------------------------------------------------ модерация
    async def _admin_out(self, uow: CollectionsUnitOfWork, collection_id: UUID) -> Json:
        row = await uow.reads.row(collection_id)
        assert row is not None  # noqa: S101 — сборник проверен в этой транзакции
        restricted = (
            row.author_id is not None and await uow.restrictions.get(row.author_id) is not None
        )
        return self._presenter.admin(row, restricted=restricted)

    async def set_hidden(self, ctx: AdminContext, collection_id: UUID, *, hidden: bool) -> Json:
        """Скрыть или показать (``CollectionUpdated`` со ``status``); повтор — без события.

        Raises:
            CollectionNotFoundError: нет или удалён (404).
            NotPublishedError: скрыть ни разу не опубликованный нельзя (409).
        """
        action = "collection.hide" if hidden else "collection.unhide"

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            collection = await self._existing(uow, collection_id)
            now = self._clock.now()
            changed = (
                collection.hide(reason=ctx.reason, actor_id=ctx.actor_id, now=now)
                if hidden
                else collection.unhide(now)
            )
            if changed:
                await uow.collections.save(collection)
                uow.track(collection)
            await self._audit(uow, ctx, action, collection_id, changed=changed)
            return await self._admin_out(uow, collection_id)

        return await self._mutate(ctx, action, collection_id, {}, perform)

    async def delete_user_collection(self, ctx: AdminContext, collection_id: UUID) -> Json:
        """Удалить пользовательский сборник (черновик, опубликованный или скрытый).

        Для застрявших сборников, которые автор не может удалить сам. Опубликованный даёт
        ``CollectionDeleted``; повтор по уже удалённому — успех без события.

        Raises:
            CollectionNotFoundError: такого сборника нет (404).
            NotUgcError: кураторский сборник (409).
        """

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            collection = await uow.collections.get(collection_id, for_update=True)
            if collection is None:
                raise CollectionNotFoundError()
            if collection.state.kind is Kind.CURATED:
                raise NotUgcError()
            changed = collection.delete(self._clock.now())
            if changed:
                await uow.collections.save(collection)
                uow.track(collection)
            await self._audit(uow, ctx, "collection.delete", collection_id, changed=changed)
            return {}

        return await self._mutate(ctx, "collection.delete", collection_id, {}, perform)

    async def set_featured(
        self, ctx: AdminContext, collection_id: UUID, *, featured: bool, rank: int | None
    ) -> Json:
        """«Рекомендуемое» (D14): кураторские и UGC, только опубликованные.

        Raises:
            CollectionNotFoundError: нет или удалён (404); NotPublishedError: 409.
        """

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            collection = await self._existing(uow, collection_id)
            collection.set_featured(featured=featured, rank=rank, now=self._clock.now())
            await uow.collections.save(collection)
            await self._audit(
                uow, ctx, "collection.featured", collection_id, featured=featured, rank=rank
            )
            return await self._admin_out(uow, collection_id)

        return await self._mutate(
            ctx, "collection.featured", collection_id, {"featured": featured, "rank": rank}, perform
        )

    # ------------------------------------------------------------------ авторы
    async def restrict(self, ctx: AdminContext, user_id: UUID) -> Json:
        """Ограничить автора: не создаёт, не правит и не публикует (опубликованное остаётся)."""

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            restriction = Restriction(
                user_id=user_id, reason=ctx.reason, actor_id=ctx.actor_id, at=self._clock.now()
            )
            await uow.restrictions.put(restriction)
            await self._audit(uow, ctx, "author.restrict", user_id)
            return restriction_json(user_id, restriction)

        return await self._mutate(ctx, "author.restrict", user_id, {}, perform)

    async def unrestrict(self, ctx: AdminContext, user_id: UUID) -> Json:
        """Снять ограничение (повтор — тот же ответ, ограничения уже нет)."""

        async def perform(uow: CollectionsUnitOfWork) -> Json:
            removed = await uow.restrictions.delete(user_id)
            await self._audit(uow, ctx, "author.unrestrict", user_id, changed=removed)
            lifted = Restriction(
                user_id=user_id, reason=ctx.reason, actor_id=ctx.actor_id, at=self._clock.now()
            )
            return restriction_json(user_id, None, lifted=lifted)

        return await self._mutate(ctx, "author.unrestrict", user_id, {}, perform)
