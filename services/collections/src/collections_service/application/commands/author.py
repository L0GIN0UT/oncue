"""Мутации автора: создание, черновик, публикация, удаление (K5, D1, D2, D8).

Каждая мутация — одна транзакция: проверки, изменение агрегата, события в outbox и ответ
для ``Idempotency-Key`` (``gv_db.idempotency``). Чужой сборник — 404 (не 403): факт его
существования не раскрывается (§12 п. 1).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from collections_service.application.common import Actor, detail_json, ensure_can_author
from collections_service.application.ports import (
    CollectionsUnitOfWork,
    Json,
    RateLimiter,
    UnitOfWorkFactory,
)
from collections_service.application.views import Presenter, draft_json
from collections_service.domain.collection import Collection
from collections_service.domain.errors import (
    CollectionNotFoundError,
    DraftConflictError,
    DraftsLimitError,
    PublishLimitError,
)
from collections_service.domain.model import Domain, DraftBody, Status
from collections_service.domain.policy import CollectionPolicy
from gv_core.clock import Clock
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent

log = structlog.get_logger(__name__)

PUBLISH_BUCKET = "publish"
DAY_S = 86_400


@dataclass(frozen=True, slots=True)
class AuthorLimits:
    """Лимиты автора (``GV_COLLECTIONS__LIMITS__*``)."""

    drafts_max: int
    publish_per_day: int


async def own_collection(
    uow: CollectionsUnitOfWork, collection_id: UUID, actor: Actor, *, for_update: bool = True
) -> Collection:
    """Сборник автора под блокировкой; чужой или несуществующий — 404."""
    collection = await uow.collections.get(collection_id, for_update=for_update)
    if collection is None or not collection.is_author(actor.user_id):
        raise CollectionNotFoundError()
    return collection


def _conflict(collection: Collection, exc: DraftConflictError) -> DraftConflictError:
    """409 ``draft_conflict`` с ``details.current`` — актуальный ``DraftOut`` (D2)."""
    return DraftConflictError(exc.message, details={"current": draft_json(collection)})


class AuthorCommands:
    """``CreateCollection``, ``SaveDraft``, ``Publish``, ``DeleteCollection``.

    Args:
        uow: Фабрика транзакций.
        clock: Часы.
        policy: Проверки содержимого.
        limiter: Лимит публикаций (fail-closed).
        limits: Лимиты автора.
        presenter: JSON карточек.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        *,
        policy: CollectionPolicy,
        limiter: RateLimiter,
        limits: AuthorLimits,
        presenter: Presenter,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._policy = policy
        self._limiter = limiter
        self._limits = limits
        self._presenter = presenter

    async def create(self, actor: Actor, *, domain: str, title: str) -> Json:
        """Новый сборник (черновик ``revision = 1``) — ``DraftOut``.

        Raises:
            AuthorRestrictedError: автор ограничен (403).
            TitleInvalidError: название не прошло ``gv_text.ugc`` (422).
            DraftsLimitError: черновиков уже ``drafts_max`` (409).
        """
        fingerprint = request_fingerprint(
            "collection.create", None, {"domain": domain, "title": title}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                await ensure_can_author(uow, actor)
                normalized = self._policy.title(title)
                # Две вкладки создают одновременно: подсчёт и вставка — под блокировкой автора.
                await uow.collections.lock_author(actor.user_id)
                if await uow.collections.count_drafts(actor.user_id) >= self._limits.drafts_max:
                    raise DraftsLimitError(
                        "слишком много черновиков", details={"max": self._limits.drafts_max}
                    )
                collection = Collection.create(
                    uuid7(),
                    domain=Domain(domain),
                    title=normalized,
                    author_id=actor.user_id,
                    now=self._clock.now(),
                )
                await uow.collections.add(collection)
                return draft_json(collection)

            response, executed = await run_idempotent(
                uow.idempotency,
                actor.user_id,
                actor.idempotency_key,
                fingerprint,
                perform,
                status_code=201,
            )
            await uow.commit()
        if executed:
            log.info("collections.created", collection_id=response["collection_id"])
        return response

    async def save_draft(
        self,
        actor: Actor,
        collection_id: UUID,
        *,
        base_revision: int,
        client_label: str | None,
        body: DraftBody,
    ) -> Json:
        """Сохранить черновик (D2) — ``DraftOut`` новой ревизии.

        Raises:
            CollectionNotFoundError: нет или чужой (404).
            CollectionDeletedError, CollectionHiddenError: удалён или скрыт (409).
            DraftConflictError: ``base_revision`` устарела (409, ``details.current``).
            DraftInvalidError: лимиты черновика (422).
        """
        fingerprint = request_fingerprint(
            "collection.save_draft",
            collection_id,
            {"base": base_revision, "label": client_label, "body": body.to_json()},
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                collection = await own_collection(uow, collection_id, actor)
                await ensure_can_author(uow, actor)
                self._policy.check_draft(body)
                try:
                    draft = collection.save_draft(
                        body,
                        base_revision=base_revision,
                        client_label=client_label,
                        now=self._clock.now(),
                    )
                except DraftConflictError as exc:
                    raise _conflict(collection, exc) from exc
                await uow.collections.save(collection)
                return draft_json(collection, draft)

            response, _ = await run_idempotent(
                uow.idempotency, actor.user_id, actor.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        return response

    async def publish(self, actor: Actor, collection_id: UUID, *, draft_revision: int) -> Json:
        """Опубликовать ревизию черновика — ``CollectionDetail`` (событие ``Published``).

        Порядок: права → черновик → проверки содержимого → лимит (fail-closed). Лимит
        тратится только на публикацию, которая пройдёт: отказ проверок его не расходует.

        Raises:
            CollectionNotFoundError: нет или чужой (404).
            CollectionDeletedError, CollectionHiddenError: удалён или скрыт (409).
            DraftConflictError: ревизия устарела (409, ``details.current``).
            PublishInvalidError: ``details.errors`` (422).
            PublishLimitError: публикаций за сутки больше лимита (429).
            LimiterUnavailableError: Redis недоступен (503).
        """
        fingerprint = request_fingerprint(
            "collection.publish", collection_id, {"revision": draft_revision}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                collection = await own_collection(uow, collection_id, actor)
                await ensure_can_author(uow, actor)
                try:
                    draft = collection.require_draft(draft_revision)
                except DraftConflictError as exc:
                    raise _conflict(collection, exc) from exc
                info = await uow.projection.items_info([i.media_item_id for i in draft.body.items])
                body = self._policy.publishable(collection.state.domain, draft.body, info)
                retry = await self._limiter.hit(
                    PUBLISH_BUCKET,
                    str(actor.user_id),
                    limit=self._limits.publish_per_day,
                    window_s=DAY_S,
                    fail_closed=True,
                )
                if retry is not None:
                    raise PublishLimitError(retry_after=retry, limit=self._limits.publish_per_day)
                collection.publish(draft_revision, body, self._clock.now())
                await uow.collections.save(collection)
                uow.track(collection)
                row = await uow.reads.row(collection_id)
                assert row is not None  # noqa: S101 — сохранён в этой транзакции
                return await detail_json(uow, self._presenter, row, actor.viewer)

            response, executed = await run_idempotent(
                uow.idempotency, actor.user_id, actor.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if executed:
            log.info(
                "collections.published",
                collection_id=str(collection_id),
                version=response["version"],
            )
        return response

    async def delete(self, actor: Actor, collection_id: UUID) -> None:
        """Удалить свой сборник (повтор — no-op); опубликованный — событие ``Deleted``.

        Raises:
            CollectionNotFoundError: нет или чужой (404).
        """
        fingerprint = request_fingerprint("collection.delete", collection_id, {})
        async with self._uow() as uow:

            async def perform() -> Json:
                collection = await own_collection(uow, collection_id, actor)
                if collection.state.status is Status.DELETED:
                    return {}
                collection.delete(self._clock.now())
                await uow.collections.save(collection)
                uow.track(collection)
                return {}

            _, executed = await run_idempotent(
                uow.idempotency,
                actor.user_id,
                actor.idempotency_key,
                fingerprint,
                perform,
                status_code=204,
            )
            await uow.commit()
        if executed:
            log.info("collections.deleted", collection_id=str(collection_id))
