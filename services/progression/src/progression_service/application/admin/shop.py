"""Admin-API 5b: предметы, паки, партии промокодов (ADR 0045, 0046, 0048).

Каждая мутация - одна транзакция: ключ идемпотентности -> проверки по действующему конфигу ->
запись -> аудит (``admin_actions``: актор и причина). Ответы возвращаются JSON-словарями, как в
остальной админке: повтор с тем же ключом отдаёт сохранённый ответ. Коды промокодов в
сохранённый ответ не попадают (в БД только хеш): повтор создания партии - 409
``progression.codes_already_shown``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7
from gv_core.random import Random
from gv_db.idempotency import request_fingerprint, run_idempotent
from progression_service.application.accrual.common import published_config
from progression_service.application.admin.common import (
    AdminContext,
    audit,
    day_bounds,
    decode_cursor,
    encode_cursor,
)
from progression_service.application.admin.metrics import ADMIN_OPERATIONS
from progression_service.application.admin.ports import AdminUnitOfWork, AdminUowFactory, Json
from progression_service.application.ports.shop import (
    CodeHasher,
    MediaInspector,
    MediaStore,
    PromoBatchRow,
    TokenSource,
)
from progression_service.domain import packs as packs_domain
from progression_service.domain import promo, shop
from progression_service.domain.packs import Pack
from progression_service.domain.promo import RewardKind
from progression_service.domain.shop import Item, MediaKind

log = structlog.get_logger(__name__)

CODES_ALREADY_SHOWN = "progression.codes_already_shown"


class PromoNotFoundError(NotFoundError):
    """Партии нет (404)."""

    default_code = "progression.promo_not_found"

    def __init__(self) -> None:
        super().__init__("партия промокодов не найдена")


class PromoItemNotFoundError(InvalidInputError):
    """Предмет-награда не существует или неактивен (422)."""

    default_code = "progression.item_not_found"

    def __init__(self) -> None:
        super().__init__("предмет-награда не найден или не активен")


class PromoNotConfiguredError(InvalidInputError):
    """В действующем конфиге нет секции ``promo`` (422)."""

    default_code = "progression.promo_not_configured"

    def __init__(self) -> None:
        super().__init__("в конфиге нет секции promo")


class PromoExpiredError(InvalidInputError):
    """Срок партии уже прошёл (422)."""

    default_code = "progression.promo_expired"

    def __init__(self) -> None:
        super().__init__("срок действия партии уже прошёл")


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


@dataclass(frozen=True, slots=True)
class ItemFields:
    """Поля предмета из тела запроса."""

    key: str
    slot: str
    rarity: str
    title: str
    media_kind: str
    level_required: int | None
    price: int | None
    active: bool
    event_key: str | None = None


@dataclass(frozen=True, slots=True)
class PackFields:
    """Поля пака из тела запроса."""

    key: str
    title: str
    style: str
    item_ids: tuple[UUID, ...]
    verses_amount: int
    price_kopecks: int
    active: bool


@dataclass(frozen=True, slots=True)
class PromoFields:
    """Поля партии из тела запроса."""

    label: str
    reward_kind: str
    verses_amount: int | None
    item_id: UUID | None
    count: int
    max_uses: int
    per_user_once: bool
    expires_at: datetime | None


async def _run(
    factory: AdminUowFactory,
    fingerprint: str,
    ctx: AdminContext,
    perform: Callable[[AdminUnitOfWork], Awaitable[Json]],
) -> tuple[Json, bool]:
    """Идемпотентное выполнение в одной транзакции (ключ - ``(актор, Idempotency-Key)``)."""
    async with factory() as uow:
        response, executed = await run_idempotent(
            uow.idempotency,
            ctx.actor_id,
            ctx.idempotency_key,
            fingerprint,
            lambda: perform(uow),
            status_code=201,
        )
        await uow.commit()
    return response, executed


class ItemCommands:
    """Предметы: создание, правка, картинка."""

    def __init__(
        self,
        factory: AdminUowFactory,
        clock: Clock,
        *,
        store: MediaStore,
        inspector: MediaInspector,
        tokens: TokenSource,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._store = store
        self._inspector = inspector
        self._tokens = tokens

    def item_json(self, item: Item, owners: int) -> Json:
        """Предмет для admin-API (``AdminItem``)."""
        return {
            "id": str(item.id),
            "key": item.key,
            "slot": item.slot,
            "rarity": item.rarity,
            "title": item.title,
            "media_kind": item.media_kind.value,
            "level_required": item.level_required,
            "price": item.price,
            "active": item.active,
            "asset_url": None if item.asset_key is None else self._store.public_url(item.asset_key),
            "poster_url": (
                None if item.poster_key is None else self._store.public_url(item.poster_key)
            ),
            "event_key": item.event_key,
            "owners": owners,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    async def create(self, fields: ItemFields, *, ctx: AdminContext) -> Json:
        """Создать предмет (без картинки - неактивным).

        Raises:
            ItemSlotUnknownError, ItemRarityUnknownError, ItemUnlockRequiredError: 422.
            ItemMediaMissingError: ``active`` без картинки (422).
            ItemKeyTakenError: ключ занят (409).
        """
        fingerprint = request_fingerprint("item.create", "-", _fields_dict(fields, ctx.reason))

        async def perform(uow: AdminUnitOfWork) -> Json:
            published = await published_config(uow)
            shop.validate_item_fields(
                slot=fields.slot,
                rarity=fields.rarity,
                level_required=fields.level_required,
                price=fields.price,
                shop_cfg=published.config.shop,
                event_key=fields.event_key,
            )
            shop.check_activation(active=fields.active, asset_key=None)
            now = self._clock.now()
            item = Item(
                id=uuid7(),
                key=fields.key,
                slot=fields.slot,
                rarity=fields.rarity,
                title=fields.title,
                media_kind=MediaKind(fields.media_kind),
                asset_key=None,
                poster_key=None,
                level_required=fields.level_required,
                price=fields.price,
                active=fields.active,
                created_at=now,
                updated_at=now,
                event_key=fields.event_key,
            )
            if not await uow.items.add(item):
                raise shop.ItemKeyTakenError
            await audit(
                uow, ctx, "item.create", f"item:{item.id}", now, action_id=uuid7(), key=item.key
            )
            return self.item_json(item, 0)

        response, executed = await _run(self._factory, fingerprint, ctx, perform)
        _done("item_created", ctx, executed=executed)
        return response

    async def update(self, item_id: UUID, fields: ItemFields, *, ctx: AdminContext) -> Json:
        """Заменить поля предмета (ключ неизменяем; владельцев правка не затрагивает).

        Raises:
            ItemNotFoundError: нет предмета (404).
            ItemKeyImmutableError, ItemMediaMissingError и ошибки полей: 422.
        """
        fingerprint = request_fingerprint("item.update", item_id, _fields_dict(fields, ctx.reason))

        async def perform(uow: AdminUnitOfWork) -> Json:
            item = await uow.items.get(item_id, for_update=True)
            if item is None:
                raise shop.ItemNotFoundError
            shop.check_key_unchanged(item.key, fields.key)
            published = await published_config(uow)
            shop.validate_item_fields(
                slot=fields.slot,
                rarity=fields.rarity,
                level_required=fields.level_required,
                price=fields.price,
                shop_cfg=published.config.shop,
                event_key=fields.event_key,
            )
            kind = MediaKind(fields.media_kind)
            if kind is not item.media_kind and item.asset_key is not None:
                raise shop.ItemMediaInvalidError(
                    "вид показа меняется только вместе с новой картинкой"
                )
            shop.check_activation(active=fields.active, asset_key=item.asset_key)
            now = self._clock.now()
            updated = replace(
                item,
                slot=fields.slot,
                rarity=fields.rarity,
                title=fields.title,
                media_kind=kind,
                level_required=fields.level_required,
                price=fields.price,
                active=fields.active,
                event_key=fields.event_key,
                updated_at=now,
            )
            await uow.items.save(updated)
            await audit(
                uow, ctx, "item.update", f"item:{item_id}", now, action_id=uuid7(), key=item.key
            )
            owners = (await uow.items.owners([item_id])).get(item_id, 0)
            return self.item_json(updated, owners)

        response, executed = await _run(self._factory, fingerprint, ctx, perform)
        _done("item_updated", ctx, executed=executed)
        return response

    async def upload_media(self, item_id: UUID, data: bytes, *, ctx: AdminContext) -> Json:
        """Загрузить картинку или анимацию предмета.

        Объект кладётся в S3 под новым случайным ключом ``c/<32 hex>.webp|png`` (при замене
        ключ меняется, кэш не устаревает); прежние объекты удаляются после коммита.

        Raises:
            ItemNotFoundError: нет предмета (404).
            ItemMediaTooLargeError: файл больше лимита (413 на HTTP).
            ItemMediaInvalidError: файл не подходит (422).
        """
        fingerprint = request_fingerprint(
            "item.media", item_id, {"reason": ctx.reason, "sha": _sha(data)}
        )
        uploaded: list[str] = []
        stale: list[str] = []

        async def perform(uow: AdminUnitOfWork) -> Json:
            item = await uow.items.get(item_id, for_update=True)
            if item is None:
                raise shop.ItemNotFoundError
            published = await published_config(uow)
            shop_cfg = published.config.shop
            if shop_cfg is None:
                raise shop.ItemSlotUnknownError
            inspected = self._inspector.inspect(data, item.media_kind, shop_cfg.media)
            shop.check_media(inspected.info, item.media_kind, shop_cfg.media)
            asset_key = shop.object_key(self._tokens.token_hex(), inspected.extension)
            await self._store.put(asset_key, data, inspected.content_type)
            uploaded.append(asset_key)
            poster_key = None
            if inspected.poster is not None:
                poster_key = shop.object_key(self._tokens.token_hex(), "webp")
                await self._store.put(poster_key, inspected.poster, "image/webp")
                uploaded.append(poster_key)
            now = self._clock.now()
            updated = replace(item, asset_key=asset_key, poster_key=poster_key, updated_at=now)
            await uow.items.save(updated)
            stale.extend(k for k in (item.asset_key, item.poster_key) if k is not None)
            await audit(
                uow, ctx, "item.media", f"item:{item_id}", now, action_id=uuid7(), key=item.key
            )
            owners = (await uow.items.owners([item_id])).get(item_id, 0)
            return self.item_json(updated, owners)

        try:
            response, executed = await _run(self._factory, fingerprint, ctx, perform)
        except Exception:
            await self._discard(uploaded)  # транзакция откатилась: новые объекты не нужны
            raise
        if executed:
            await self._discard(stale)
        _done("item_media_uploaded", ctx, executed=executed)
        return response

    async def _discard(self, keys: Sequence[str]) -> None:
        """Удалить объекты; сбой хранилища не ломает ответ (объект - мусор, не данные)."""
        try:
            await self._store.delete(keys)
        except Exception:
            log.warning("progression.item_media_cleanup_failed", count=len(keys), exc_info=True)


class ItemQueries:
    """Список предметов."""

    def __init__(self, factory: AdminUowFactory, commands: ItemCommands) -> None:
        self._factory = factory
        self._commands = commands

    async def page(
        self, *, slot: str | None, active: bool | None, cursor: str | None, limit: int
    ) -> Json:
        """Страница предметов, новые первыми."""
        before = None if cursor is None else decode_cursor(cursor)
        async with self._factory() as uow:
            rows = await uow.items.page(slot=slot, active=active, before=before, limit=limit + 1)
            more = rows[limit:]
            rows = rows[:limit]
            owners = await uow.items.owners([i.id for i in rows])
        return {
            "items": [self._commands.item_json(i, owners.get(i.id, 0)) for i in rows],
            "next_cursor": encode_cursor(rows[-1].created_at, rows[-1].id) if more else None,
        }


def _fields_dict(fields: ItemFields, reason: str) -> dict[str, Any]:
    return {
        "key": fields.key,
        "slot": fields.slot,
        "rarity": fields.rarity,
        "title": fields.title,
        "media_kind": fields.media_kind,
        "level_required": fields.level_required,
        "price": fields.price,
        "active": fields.active,
        "event_key": fields.event_key,
        "reason": reason,
    }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _done(operation: str, ctx: AdminContext, *, executed: bool) -> None:
    if executed:
        ADMIN_OPERATIONS.labels(operation, "ok").inc()
        log.info(f"progression.admin.{operation}", actor_id=str(ctx.actor_id))


# ---------------------------------------------------------------------------- паки
def _pack_json(pack: Pack) -> Json:
    return {
        "id": str(pack.id),
        "key": pack.key,
        "title": pack.title,
        "style": pack.style,
        "item_ids": [str(i) for i in pack.item_ids],
        "verses_amount": pack.verses_amount,
        "price_kopecks": pack.price_kopecks,
        "active": pack.active,
        "created_at": pack.created_at.isoformat(),
        "updated_at": pack.updated_at.isoformat(),
    }


class PackCommands:
    """Паки: создание и замена (продажа скрыта, пока billing выключен)."""

    def __init__(self, factory: AdminUowFactory, clock: Clock) -> None:
        self._factory = factory
        self._clock = clock

    async def create(self, fields: PackFields, *, ctx: AdminContext) -> Json:
        """Создать пак.

        Raises:
            PackInvalidError: пустой состав, повторы или несуществующий предмет (422).
            PackKeyTakenError: ключ занят (409).
        """
        fingerprint = request_fingerprint("pack.create", "-", _pack_dict(fields, ctx.reason))

        async def perform(uow: AdminUnitOfWork) -> Json:
            await _check_members(uow, fields.item_ids)
            now = self._clock.now()
            pack = _pack(uuid7(), fields, now, now)
            if not await uow.packs.add(pack):
                raise packs_domain.PackKeyTakenError
            await audit(uow, ctx, "pack.create", f"pack:{pack.id}", now, action_id=uuid7())
            return _pack_json(pack)

        response, executed = await _run(self._factory, fingerprint, ctx, perform)
        _done("pack_created", ctx, executed=executed)
        return response

    async def replace(self, pack_id: UUID, fields: PackFields, *, ctx: AdminContext) -> Json:
        """Заменить пак целиком.

        Raises:
            PackNotFoundError: нет пака (404).
            PackInvalidError: состав неверен (422).
            PackKeyTakenError: ключ занят другим паком (409).
        """
        fingerprint = request_fingerprint("pack.replace", pack_id, _pack_dict(fields, ctx.reason))

        async def perform(uow: AdminUnitOfWork) -> Json:
            current = await uow.packs.get(pack_id)
            if current is None:
                raise packs_domain.PackNotFoundError
            await _check_members(uow, fields.item_ids)
            now = self._clock.now()
            pack = _pack(pack_id, fields, current.created_at, now)
            if not await uow.packs.replace(pack):
                raise packs_domain.PackKeyTakenError
            await audit(uow, ctx, "pack.replace", f"pack:{pack_id}", now, action_id=uuid7())
            return _pack_json(pack)

        response, executed = await _run(self._factory, fingerprint, ctx, perform)
        _done("pack_replaced", ctx, executed=executed)
        return response


class PackQueries:
    """Список паков для админки."""

    def __init__(self, factory: AdminUowFactory) -> None:
        self._factory = factory

    async def all(self) -> Json:
        """Все паки (в том числе неактивные)."""
        async with self._factory() as uow:
            packs = await uow.packs.listing(active_only=False)
        return {"items": [_pack_json(p) for p in packs]}


def _pack(pack_id: UUID, f: PackFields, created_at: datetime, updated_at: datetime) -> Pack:
    return Pack(
        id=pack_id,
        key=f.key,
        title=f.title,
        style=f.style,
        item_ids=f.item_ids,
        verses_amount=f.verses_amount,
        price_kopecks=f.price_kopecks,
        active=f.active,
        created_at=created_at,
        updated_at=updated_at,
    )


def _pack_dict(f: PackFields, reason: str) -> dict[str, Any]:
    return {
        "key": f.key,
        "title": f.title,
        "style": f.style,
        "item_ids": [str(i) for i in f.item_ids],
        "verses_amount": f.verses_amount,
        "price_kopecks": f.price_kopecks,
        "active": f.active,
        "reason": reason,
    }


async def _check_members(uow: AdminUnitOfWork, item_ids: Sequence[UUID]) -> None:
    packs_domain.check_pack_items(item_ids)
    found = await uow.items.by_ids(item_ids)
    if len(found) != len(item_ids):
        raise packs_domain.PackInvalidError("предмет не найден")
    if any(i.event_key is not None for i in found.values()):
        raise packs_domain.PackInvalidError("предмет события нельзя положить в пак")


# ---------------------------------------------------------------------------- промокоды
def _batch_json(row: PromoBatchRow, now: datetime) -> Json:
    if row.revoked_at is not None:
        status = "revoked"
    elif row.expires_at is not None and row.expires_at <= now:
        status = "expired"
    else:
        status = "active"
    return {
        "id": str(row.id),
        "label": row.label,
        "reward": {
            "kind": row.reward_kind.value,
            "verses_amount": row.verses_amount,
            "item_id": None if row.item_id is None else str(row.item_id),
        },
        "codes_total": row.codes_total,
        "activations": row.activations,
        "max_uses": row.max_uses,
        "per_user_once": row.per_user_once,
        "expires_at": _iso(row.expires_at),
        "status": status,
        "created_at": row.created_at.isoformat(),
    }


class PromoCommands:
    """Партии промокодов: выпуск и отзыв."""

    def __init__(
        self,
        factory: AdminUowFactory,
        clock: Clock,
        *,
        hasher: CodeHasher,
        rng: Random,
    ) -> None:
        self._factory = factory
        self._clock = clock
        self._hasher = hasher
        self._rng = rng

    async def create(self, fields: PromoFields, *, ctx: AdminContext) -> Json:
        """Сгенерировать партию кодов; открытые коды возвращаются один раз.

        Raises:
            PromoNotConfiguredError: нет секции ``promo`` в конфиге (422).
            PromoRewardInvalidError, PromoBatchTooLargeError, PromoItemNotFoundError,
                PromoExpiredError: 422.
            PromoActorDailyCapExceededError, PromoTotalDailyCapExceededError: суточный потолок
                эмиссии (422).
            ConflictError: ``progression.codes_already_shown`` - повтор ключа (409).
        """
        fingerprint = request_fingerprint(
            "promo.create",
            "-",
            {
                "label": fields.label,
                "reward_kind": fields.reward_kind,
                "verses_amount": fields.verses_amount,
                "item_id": None if fields.item_id is None else str(fields.item_id),
                "count": fields.count,
                "max_uses": fields.max_uses,
                "per_user_once": fields.per_user_once,
                "expires_at": _iso(fields.expires_at),
                "reason": ctx.reason,
            },
        )
        shown: list[str] = []

        async def perform(uow: AdminUnitOfWork) -> Json:
            published = await published_config(uow)
            cfg = published.config.promo
            if cfg is None:
                raise PromoNotConfiguredError
            kind = RewardKind(fields.reward_kind)
            promo.check_reward(kind, verses_amount=fields.verses_amount, item_id=fields.item_id)
            promo.check_batch_emission(
                fields.count, fields.max_uses, fields.verses_amount, ceiling=cfg.max_batch_verses
            )
            now = self._clock.now()
            if fields.expires_at is not None and fields.expires_at <= now:
                raise PromoExpiredError
            if fields.item_id is not None:
                item = await uow.items.get(fields.item_id)
                # Предметы событий промокодом не выдаются (ADR 0056).
                if item is None or not item.active or item.event_key is not None:
                    raise PromoItemNotFoundError
            emission = promo.batch_emission(fields.count, fields.max_uses, fields.verses_amount)
            if emission > 0:
                # Сутки считаются в config.timezone; проверка и вставка - под блокировкой суток,
                # иначе параллельные партии вместе превысят потолок (ADR 0048).
                start, end = day_bounds(now, published.config.tz)
                await uow.promos.lock_emission_day(
                    now.astimezone(published.config.tz).date().isoformat()
                )
                promo.check_daily_emission(
                    emission,
                    actor_used=await uow.promos.emitted_verses(start, end, created_by=ctx.actor_id),
                    total_used=await uow.promos.emitted_verses(start, end),
                    actor_limit=cfg.max_daily_verses_per_actor,
                    total_limit=cfg.max_daily_verses_total,
                )
            codes = promo.generate_codes(self._rng, count=fields.count, length=cfg.code_length)
            batch = PromoBatchRow(
                id=uuid7(),
                label=fields.label,
                reward_kind=kind,
                verses_amount=fields.verses_amount,
                item_id=fields.item_id,
                max_uses=fields.max_uses,
                per_user_once=fields.per_user_once,
                expires_at=fields.expires_at,
                codes_total=fields.count,
                activations=0,
                revoked_at=None,
                created_at=now,
            )
            await uow.promos.add_batch(
                batch, ctx.actor_id, [(uuid7(), self._hasher.hash(c)) for c in codes]
            )
            await audit(
                uow,
                ctx,
                "promo.create",
                f"promo:{batch.id}",
                now,
                action_id=uuid7(),
                count=fields.count,
                max_uses=fields.max_uses,
            )
            shown.extend(promo.format_code(c) for c in codes)
            # В сохранённый ответ коды не попадают: повтор не раскроет их заново.
            return {"id": str(batch.id), "label": batch.label, "codes": []}

        response, executed = await _run(self._factory, fingerprint, ctx, perform)
        if not executed:
            raise ConflictError(
                "коды этой партии уже были показаны и не хранятся в открытом виде",
                code=CODES_ALREADY_SHOWN,
            )
        _done("promo_created", ctx, executed=executed)
        return {**response, "codes": shown}

    async def revoke(self, promo_id: UUID, *, ctx: AdminContext) -> None:
        """Отозвать партию: новые активации невозможны, выданное не отзывается.

        Raises:
            PromoNotFoundError: партии нет (404).
        """
        fingerprint = request_fingerprint("promo.revoke", promo_id, {"reason": ctx.reason})

        async def perform(uow: AdminUnitOfWork) -> Json:
            now = self._clock.now()
            if not await uow.promos.revoke(promo_id, now):
                raise PromoNotFoundError
            await audit(uow, ctx, "promo.revoke", f"promo:{promo_id}", now, action_id=uuid7())
            return {}

        _, executed = await _run(self._factory, fingerprint, ctx, perform)
        _done("promo_revoked", ctx, executed=executed)


class PromoQueries:
    """Список партий (без кодов)."""

    def __init__(self, factory: AdminUowFactory, clock: Clock) -> None:
        self._factory = factory
        self._clock = clock

    async def page(self, *, cursor: str | None, limit: int) -> Json:
        """Страница партий, новые первыми."""
        before = None if cursor is None else decode_cursor(cursor)
        async with self._factory() as uow:
            rows = await uow.promos.page(before=before, limit=limit + 1)
        now = self._clock.now()
        more = rows[limit:]
        rows = rows[:limit]
        return {
            "items": [_batch_json(r, now) for r in rows],
            "next_cursor": encode_cursor(rows[-1].created_at, rows[-1].id) if more else None,
        }
