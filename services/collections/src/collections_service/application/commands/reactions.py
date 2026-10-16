"""Лайки и избранное (D13): естественная идемпотентность PK + ``Idempotency-Key``.

Счётчик лайков меняется в той же транзакции атомарным UPDATE (без загрузки агрегата);
``CollectionLiked`` публикуется только при новом лайке, снятие — без события. Автор не
лайкает свой сборник. Лимит частоты — Redis fail-open с метрикой (§12 п. 4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from collections_service.application.common import Actor
from collections_service.application.ports import (
    CollectionRow,
    CollectionsUnitOfWork,
    Json,
    RateLimiter,
    UnitOfWorkFactory,
)
from collections_service.domain.access import can_view
from collections_service.domain.collection import liked_event
from collections_service.domain.errors import (
    CollectionNotFoundError,
    CollectionsRateLimitedError,
    OwnCollectionError,
)
from collections_service.domain.model import Status
from gv_core.clock import Clock
from gv_db.idempotency import request_fingerprint, run_idempotent

REACTIONS_BUCKET = "reactions"
MINUTE_S = 60


@dataclass(frozen=True, slots=True)
class ReactionLimits:
    """Лимиты реакций."""

    per_min: int


class ReactionCommands:
    """``Like``/``Unlike``/``Favorite``/``Unfavorite``.

    Args:
        uow: Фабрика транзакций.
        clock: Часы.
        limiter: Лимит частоты (fail-open).
        limits: Лимиты.
    """

    def __init__(
        self, uow: UnitOfWorkFactory, clock: Clock, limiter: RateLimiter, limits: ReactionLimits
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._limiter = limiter
        self._limits = limits

    async def _throttle(self, actor: Actor) -> None:
        retry = await self._limiter.hit(
            REACTIONS_BUCKET, str(actor.user_id), limit=self._limits.per_min, window_s=MINUTE_S
        )
        if retry is not None:
            raise CollectionsRateLimitedError(retry_after=retry)

    async def _run(
        self,
        actor: Actor,
        action: str,
        collection_id: UUID,
        perform: Callable[[CollectionsUnitOfWork, CollectionRow], Awaitable[Json]],
        *,
        need_published: bool,
    ) -> Json:
        await self._throttle(actor)
        fingerprint = request_fingerprint(action, collection_id, {})
        async with self._uow() as uow:

            async def run() -> Json:
                row = await uow.reads.row(collection_id)
                if row is None or not can_view(row.status, row.author_id, actor.viewer):
                    raise CollectionNotFoundError()
                if need_published and row.status is not Status.PUBLISHED:
                    raise CollectionNotFoundError()
                return await perform(uow, row)

            response, _ = await run_idempotent(
                uow.idempotency, actor.user_id, actor.idempotency_key, fingerprint, run
            )
            await uow.commit()
        return response

    async def like(self, actor: Actor, collection_id: UUID) -> Json:
        """Лайк — ``LikeOut``; новый лайк — событие ``CollectionLiked``.

        Raises:
            CollectionNotFoundError: не опубликован или не виден (404).
            OwnCollectionError: свой сборник (403).
            CollectionsRateLimitedError: лимит частоты (429).
        """

        async def perform(uow: CollectionsUnitOfWork, row: CollectionRow) -> Json:
            if row.author_id == actor.user_id:
                raise OwnCollectionError()
            now = self._clock.now()
            if not await uow.reactions.add_like(actor.user_id, collection_id, now):
                return {
                    "liked": True,
                    "likes_count": await uow.collections.likes_count(collection_id),
                }
            version, likes = await uow.collections.like_added(collection_id)
            await uow.stats.bump_daily(collection_id, now.date(), likes=1, plays=0)
            uow.emit(
                liked_event(
                    collection_id,
                    version=version,
                    user_id=actor.user_id,
                    likes_count=likes,
                    now=now,
                )
            )
            return {"liked": True, "likes_count": likes}

        return await self._run(
            actor, "collection.like", collection_id, perform, need_published=True
        )

    async def unlike(self, actor: Actor, collection_id: UUID) -> Json:
        """Снять лайк — ``LikeOut`` (без события)."""

        async def perform(uow: CollectionsUnitOfWork, _: CollectionRow) -> Json:
            if await uow.reactions.remove_like(actor.user_id, collection_id):
                likes = await uow.collections.like_removed(collection_id)
            else:
                likes = await uow.collections.likes_count(collection_id)
            return {"liked": False, "likes_count": likes}

        return await self._run(
            actor, "collection.unlike", collection_id, perform, need_published=False
        )

    async def favorite(self, actor: Actor, collection_id: UUID) -> Json:
        """В избранное — ``FavoriteOut``."""

        async def perform(uow: CollectionsUnitOfWork, _: CollectionRow) -> Json:
            await uow.reactions.add_favorite(actor.user_id, collection_id, self._clock.now())
            return {"favorite": True}

        return await self._run(
            actor, "collection.favorite", collection_id, perform, need_published=True
        )

    async def unfavorite(self, actor: Actor, collection_id: UUID) -> Json:
        """Из избранного — ``FavoriteOut``."""

        async def perform(uow: CollectionsUnitOfWork, _: CollectionRow) -> Json:
            await uow.reactions.remove_favorite(actor.user_id, collection_id)
            return {"favorite": False}

        return await self._run(
            actor, "collection.unfavorite", collection_id, perform, need_published=False
        )
