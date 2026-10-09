"""Админ-мутации единиц: блокировка, разблокировка, замена алиасов.

Все мутации идемпотентны по ``(actor_id, Idempotency-Key)`` через ``gv_db.idempotency``:
ключ и ответ сохраняются в той же транзакции, что и изменение; повтор с тем же ключом и
телом возвращает сохранённый ответ, с другим телом — 409 ``idempotency.key_reused``.
Каждое выполненное действие пишется в аудит (``admin_actions``) в той же транзакции,
вместе с причиной.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

from catalog_service.application.dto import RecordedAction, admin_item_view
from catalog_service.application.ports import CatalogUnitOfWork, PrefixCache, UnitOfWorkFactory
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import AliasDraft
from catalog_service.domain.normalization import TitleNormalizer
from gv_core.errors import NotFoundError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AdminContext:
    """Кто и зачем выполняет мутацию."""

    actor_id: uuid.UUID
    idempotency_key: str
    reason: str


async def _idempotent(
    uow: CatalogUnitOfWork,
    ctx: AdminContext,
    *,
    action: str,
    target_id: uuid.UUID,
    body: dict[str, Any],
    perform: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """Выполнить ``perform`` и записать аудит один раз на ключ (``run_idempotent``).

    Returns:
        Ответ и признак «выполнено сейчас» (``False`` — повтор, ответ из хранилища ключей).
    """
    fingerprint = request_fingerprint(action, target_id, body)

    async def perform_and_audit() -> dict[str, Any]:
        response = await perform()
        await uow.actions.record(
            RecordedAction(
                id=uuid7(),
                actor_id=ctx.actor_id,
                idempotency_key=ctx.idempotency_key,
                action=action,
                target_id=target_id,
                reason=ctx.reason,
                request_hash=fingerprint,
                response=response,
            )
        )
        return response

    return await run_idempotent(
        uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform_and_audit
    )


async def _load(uow: CatalogUnitOfWork, item_id: uuid.UUID) -> MediaItem:
    item = await uow.items.get(item_id)
    if item is None:
        raise NotFoundError("единица не найдена", code="catalog.item_not_found")
    return item


class _AdminCommand:
    def __init__(self, uow_factory: UnitOfWorkFactory, cache: PrefixCache | None) -> None:
        self._uow = uow_factory
        self._cache = cache

    async def _after_commit(self, *, changed: bool) -> None:
        # Кэш автокомплита сбрасывается сразу, не дожидаясь индексации: иначе
        # заблокированная единица ещё TTL секунд жила бы в подсказках.
        if changed and self._cache is not None:
            await self._cache.invalidate()


class BlockItem(_AdminCommand):
    """Заблокировать единицу (право ``catalog.block_item``)."""

    async def __call__(
        self, item_id: uuid.UUID, reason_code: str, ctx: AdminContext
    ) -> dict[str, Any]:
        """Заблокировать и вернуть карточку."""
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                item = await _load(uow, item_id)
                item.block(reason_code, ctx.actor_id)
                await uow.items.save(item)
                uow.track(item)
                return admin_item_view(item)

            response, done = await _idempotent(
                uow,
                ctx,
                action="item.block",
                target_id=item_id,
                body={"reason_code": reason_code, "reason": ctx.reason},
                perform=perform,
            )
            await uow.commit()
        if done:
            log.info("catalog.item_blocked", media_item_id=str(item_id), reason_code=reason_code)
        await self._after_commit(changed=done)
        return response


class UnblockItem(_AdminCommand):
    """Снять блокировку (право ``catalog.block_item``)."""

    async def __call__(self, item_id: uuid.UUID, ctx: AdminContext) -> dict[str, Any]:
        """Разблокировать и вернуть карточку."""
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                item = await _load(uow, item_id)
                item.unblock(ctx.actor_id)
                await uow.items.save(item)
                uow.track(item)
                return admin_item_view(item)

            response, done = await _idempotent(
                uow,
                ctx,
                action="item.unblock",
                target_id=item_id,
                body={"reason": ctx.reason},
                perform=perform,
            )
            await uow.commit()
        if done:
            log.info("catalog.item_unblocked", media_item_id=str(item_id))
        await self._after_commit(changed=done)
        return response


class ReplaceAliases(_AdminCommand):
    """Заменить алиасы единицы ручным списком (право ``catalog.edit_aliases``)."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        cache: PrefixCache | None,
        normalizer: TitleNormalizer,
    ) -> None:
        super().__init__(uow_factory, cache)
        self._normalizer = normalizer

    async def __call__(
        self, item_id: uuid.UUID, aliases: Sequence[AliasDraft], ctx: AdminContext
    ) -> dict[str, Any]:
        """Заменить и вернуть карточку."""
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                item = await _load(uow, item_id)
                if item.replace_aliases(aliases, self._normalizer):
                    await uow.items.save(item)
                    uow.track(item)
                return admin_item_view(item)

            response, done = await _idempotent(
                uow,
                ctx,
                action="item.replace_aliases",
                target_id=item_id,
                body={
                    "aliases": [[a.text, a.kind.value, a.lang] for a in aliases],
                    "reason": ctx.reason,
                },
                perform=perform,
            )
            await uow.commit()
        await self._after_commit(changed=done)
        return response
