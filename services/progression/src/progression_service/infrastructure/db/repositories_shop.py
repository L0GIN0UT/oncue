"""Postgres-репозитории магазина, инвентаря, оформления, промокодов и паков (поток S1)."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, literal, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from progression_service.application.ports.shop import PromoBatchRow
from progression_service.domain.inventory import InventoryItem, Source
from progression_service.domain.packs import Pack
from progression_service.domain.promo import PromoCode, RewardKind
from progression_service.domain.shop import Item, MediaKind
from progression_service.infrastructure.db.tables_shop import (
    equipped,
    inventory,
    items,
    pack_items,
    packs,
    promo_batches,
    promo_codes,
    promo_redemptions,
)


def _item(r: Any) -> Item:
    return Item(
        id=r.id,
        key=r.key,
        slot=r.slot,
        rarity=r.rarity,
        title=r.title,
        media_kind=MediaKind(r.media_kind),
        asset_key=r.asset_key,
        poster_key=r.poster_key,
        level_required=r.level_required,
        price=r.price,
        active=r.active,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _item_values(item: Item) -> dict[str, Any]:
    return {
        "slot": item.slot,
        "rarity": item.rarity,
        "title": item.title,
        "media_kind": item.media_kind.value,
        "asset_key": item.asset_key,
        "poster_key": item.poster_key,
        "level_required": item.level_required,
        "price": item.price,
        "active": item.active,
        "updated_at": item.updated_at,
    }


class SqlItems:
    """Предметы косметики."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, item_id: UUID, *, for_update: bool = False) -> Item | None:
        """Предмет по id; ``for_update`` блокирует строку до конца транзакции."""
        stmt = select(items).where(items.c.id == item_id)
        if for_update:
            stmt = stmt.with_for_update()
        row = (await self._s.execute(stmt)).first()
        return None if row is None else _item(row)

    async def get_by_key(self, key: str) -> Item | None:
        """Предмет по ключу."""
        row = (await self._s.execute(select(items).where(items.c.key == key))).first()
        return None if row is None else _item(row)

    async def by_ids(self, item_ids: Collection[UUID]) -> dict[UUID, Item]:
        """Предметы по id."""
        if not item_ids:
            return {}
        rows = await self._s.execute(select(items).where(items.c.id.in_(list(item_ids))))
        return {r.id: _item(r) for r in rows}

    async def list_active(self, slot: str | None) -> list[Item]:
        """Активные предметы, старые первыми."""
        stmt = select(items).where(items.c.active.is_(True))
        if slot is not None:
            stmt = stmt.where(items.c.slot == slot)
        rows = await self._s.execute(stmt.order_by(items.c.created_at, items.c.id))
        return [_item(r) for r in rows]

    async def add(self, item: Item) -> bool:
        """Создать предмет; ``False`` - ключ занят."""
        row = (
            await self._s.execute(
                pg_insert(items)
                .values(id=item.id, key=item.key, created_at=item.created_at, **_item_values(item))
                .on_conflict_do_nothing(index_elements=[items.c.key])
                .returning(items.c.id)
            )
        ).first()
        return row is not None

    async def save(self, item: Item) -> None:
        """Записать изменяемые поля."""
        await self._s.execute(
            update(items).where(items.c.id == item.id).values(**_item_values(item))
        )

    async def page(
        self,
        *,
        slot: str | None,
        active: bool | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Item]:
        """Страница предметов, новые первыми."""
        stmt = select(items)
        if slot is not None:
            stmt = stmt.where(items.c.slot == slot)
        if active is not None:
            stmt = stmt.where(items.c.active.is_(active))
        if before is not None:
            stmt = stmt.where(tuple_(items.c.created_at, items.c.id) < tuple_(*before))
        rows = await self._s.execute(
            stmt.order_by(items.c.created_at.desc(), items.c.id.desc()).limit(limit)
        )
        return [_item(r) for r in rows]

    async def owners(self, item_ids: Collection[UUID]) -> dict[UUID, int]:
        """Число владельцев по предметам."""
        if not item_ids:
            return {}
        rows = await self._s.execute(
            select(inventory.c.item_id, func.count())
            .where(inventory.c.item_id.in_(list(item_ids)))
            .group_by(inventory.c.item_id)
        )
        return {r[0]: int(r[1]) for r in rows}


class SqlInventory:
    """Инвентарь игрока."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _entry(r: Any) -> InventoryItem:
        return InventoryItem(
            user_id=r.user_id,
            item_id=r.item_id,
            source=Source(r.source),
            acquired_at=r.acquired_at,
            expires_at=r.expires_at,
            convert_condition=r.convert_condition,
        )

    @staticmethod
    def _alive(now: datetime) -> Any:
        return (inventory.c.expires_at.is_(None)) | (inventory.c.expires_at > now)

    async def get(self, user_id: UUID, item_id: UUID, now: datetime) -> InventoryItem | None:
        """Строка инвентаря, если не просрочена."""
        row = (
            await self._s.execute(
                select(inventory).where(
                    inventory.c.user_id == user_id,
                    inventory.c.item_id == item_id,
                    self._alive(now),
                )
            )
        ).first()
        return None if row is None else self._entry(row)

    async def add(self, entry: InventoryItem) -> bool:
        """Выдать предмет; ``False`` - уже есть."""
        row = (
            await self._s.execute(
                pg_insert(inventory)
                .values(
                    user_id=entry.user_id,
                    item_id=entry.item_id,
                    source=entry.source.value,
                    acquired_at=entry.acquired_at,
                    expires_at=entry.expires_at,
                    convert_condition=entry.convert_condition,
                )
                .on_conflict_do_nothing(index_elements=[inventory.c.user_id, inventory.c.item_id])
                .returning(inventory.c.item_id)
            )
        ).first()
        return row is not None

    async def grant_level_items(self, user_id: UUID, level: int, now: datetime) -> int:
        """Ленивая выдача по уровню: одним ``INSERT … SELECT … ON CONFLICT DO NOTHING``."""
        stmt = (
            pg_insert(inventory)
            .from_select(
                ["user_id", "item_id", "source", "acquired_at"],
                select(
                    literal(user_id, inventory.c.user_id.type),
                    items.c.id,
                    literal(Source.LEVEL.value, inventory.c.source.type),
                    literal(now, inventory.c.acquired_at.type),
                ).where(
                    items.c.active.is_(True),
                    items.c.level_required.is_not(None),
                    items.c.level_required <= level,
                ),
            )
            .on_conflict_do_nothing(index_elements=[inventory.c.user_id, inventory.c.item_id])
            .returning(inventory.c.item_id)
        )
        return len((await self._s.execute(stmt)).all())

    async def entries(self, user_id: UUID, now: datetime) -> list[InventoryItem]:
        """Непросроченные предметы, старые первыми."""
        rows = await self._s.execute(
            select(inventory)
            .where(inventory.c.user_id == user_id, self._alive(now))
            .order_by(inventory.c.acquired_at, inventory.c.item_id)
        )
        return [self._entry(r) for r in rows]

    async def owned_ids(self, user_id: UUID, now: datetime) -> set[UUID]:
        """Идентификаторы непросроченных предметов."""
        rows = await self._s.execute(
            select(inventory.c.item_id).where(inventory.c.user_id == user_id, self._alive(now))
        )
        return {r[0] for r in rows}


class SqlEquipped:
    """Оформление."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def mine(self, user_id: UUID) -> dict[str, UUID]:
        """Слот -> предмет."""
        rows = await self._s.execute(
            select(equipped.c.slot, equipped.c.item_id).where(equipped.c.user_id == user_id)
        )
        return {r.slot: r.item_id for r in rows}

    async def many(self, user_ids: Collection[UUID]) -> dict[UUID, dict[str, UUID]]:
        """Оформление нескольких игроков."""
        if not user_ids:
            return {}
        rows = await self._s.execute(
            select(equipped.c.user_id, equipped.c.slot, equipped.c.item_id).where(
                equipped.c.user_id.in_(list(user_ids))
            )
        )
        out: dict[UUID, dict[str, UUID]] = {}
        for r in rows:
            out.setdefault(r.user_id, {})[r.slot] = r.item_id
        return out

    async def put(self, user_id: UUID, slot: str, item_id: UUID, now: datetime) -> None:
        """Надеть (заменить) предмет в слоте."""
        insert = pg_insert(equipped).values(
            user_id=user_id, slot=slot, item_id=item_id, equipped_at=now
        )
        await self._s.execute(
            insert.on_conflict_do_update(
                index_elements=[equipped.c.user_id, equipped.c.slot],
                set_={"item_id": item_id, "equipped_at": now},
            )
        )

    async def clear(self, user_id: UUID, slot: str) -> None:
        """Освободить слот."""
        await self._s.execute(
            delete(equipped).where(equipped.c.user_id == user_id, equipped.c.slot == slot)
        )


class SqlPromos:
    """Партии, коды и погашения."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def lock_code(self, code_hash: str) -> PromoCode | None:
        """Код с условиями партии; строка кода блокируется до конца транзакции."""
        row = (
            await self._s.execute(
                select(
                    promo_codes.c.id,
                    promo_codes.c.batch_id,
                    promo_codes.c.max_uses,
                    promo_codes.c.activations,
                    promo_batches.c.reward_kind,
                    promo_batches.c.verses_amount,
                    promo_batches.c.item_id,
                    promo_batches.c.per_user_once,
                    promo_batches.c.expires_at,
                    promo_batches.c.revoked_at,
                )
                .join(promo_batches, promo_batches.c.id == promo_codes.c.batch_id)
                .where(promo_codes.c.code_hash == code_hash)
                .with_for_update(of=promo_codes)
            )
        ).first()
        if row is None:
            return None
        return PromoCode(
            code_id=row.id,
            batch_id=row.batch_id,
            reward_kind=RewardKind(row.reward_kind),
            verses_amount=row.verses_amount,
            item_id=row.item_id,
            max_uses=row.max_uses,
            activations=row.activations,
            per_user_once=row.per_user_once,
            expires_at=row.expires_at,
            revoked=row.revoked_at is not None,
            code_hash=code_hash,
        )

    async def redeemed(self, code_hash: str, user_id: UUID) -> bool:
        """Игрок уже погасил код."""
        found = await self._s.scalar(
            select(promo_redemptions.c.user_id).where(
                promo_redemptions.c.code_hash == code_hash,
                promo_redemptions.c.user_id == user_id,
            )
        )
        return found is not None

    async def batch_redeemed_by(self, batch_id: UUID, user_id: UUID) -> bool:
        """Игрок уже гасил код партии."""
        found = await self._s.scalar(
            select(promo_redemptions.c.user_id)
            .where(promo_redemptions.c.batch_id == batch_id, promo_redemptions.c.user_id == user_id)
            .limit(1)
        )
        return found is not None

    async def add_redemption(
        self, code_hash: str, user_id: UUID, code_id: UUID, batch_id: UUID, now: datetime
    ) -> None:
        """Погашение и счётчик кода (код уже заблокирован ``lock_code``)."""
        await self._s.execute(
            promo_redemptions.insert().values(
                code_hash=code_hash,
                user_id=user_id,
                code_id=code_id,
                batch_id=batch_id,
                redeemed_at=now,
            )
        )
        await self._s.execute(
            update(promo_codes)
            .where(promo_codes.c.id == code_id)
            .values(activations=promo_codes.c.activations + 1)
        )

    async def add_batch(
        self, row: PromoBatchRow, created_by: UUID, codes: Sequence[tuple[UUID, str]]
    ) -> None:
        """Партия и коды."""
        await self._s.execute(
            promo_batches.insert().values(
                id=row.id,
                label=row.label,
                reward_kind=row.reward_kind.value,
                verses_amount=row.verses_amount,
                item_id=row.item_id,
                max_uses=row.max_uses,
                per_user_once=row.per_user_once,
                expires_at=row.expires_at,
                codes_total=row.codes_total,
                revoked_at=None,
                created_by=created_by,
                created_at=row.created_at,
            )
        )
        await self._s.execute(
            promo_codes.insert(),
            [
                {
                    "id": code_id,
                    "batch_id": row.id,
                    "code_hash": code_hash,
                    "max_uses": row.max_uses,
                    "activations": 0,
                    "created_at": row.created_at,
                }
                for code_id, code_hash in codes
            ],
        )

    async def page(
        self, *, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[PromoBatchRow]:
        """Партии, новые первыми."""
        activations = (
            select(promo_codes.c.batch_id, func.sum(promo_codes.c.activations).label("n"))
            .group_by(promo_codes.c.batch_id)
            .subquery()
        )
        stmt = select(promo_batches, func.coalesce(activations.c.n, 0).label("activations")).join(
            activations, activations.c.batch_id == promo_batches.c.id, isouter=True
        )
        if before is not None:
            stmt = stmt.where(
                tuple_(promo_batches.c.created_at, promo_batches.c.id) < tuple_(*before)
            )
        rows = await self._s.execute(
            stmt.order_by(promo_batches.c.created_at.desc(), promo_batches.c.id.desc()).limit(limit)
        )
        return [
            PromoBatchRow(
                id=r.id,
                label=r.label,
                reward_kind=RewardKind(r.reward_kind),
                verses_amount=r.verses_amount,
                item_id=r.item_id,
                max_uses=r.max_uses,
                per_user_once=r.per_user_once,
                expires_at=r.expires_at,
                codes_total=r.codes_total,
                activations=int(r.activations),
                revoked_at=r.revoked_at,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def revoke(self, batch_id: UUID, now: datetime) -> bool:
        """Отозвать партию (повторный отзыв сохраняет первый момент)."""
        found = await self._s.scalar(
            select(promo_batches.c.id).where(promo_batches.c.id == batch_id).with_for_update()
        )
        if found is None:
            return False
        await self._s.execute(
            update(promo_batches)
            .where(promo_batches.c.id == batch_id, promo_batches.c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        return True


class SqlPacks:
    """Паки косметики."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _members(self, pack_ids: Collection[UUID]) -> dict[UUID, list[UUID]]:
        if not pack_ids:
            return {}
        rows = await self._s.execute(
            select(pack_items.c.pack_id, pack_items.c.item_id)
            .where(pack_items.c.pack_id.in_(list(pack_ids)))
            .order_by(pack_items.c.pack_id, pack_items.c.position)
        )
        out: dict[UUID, list[UUID]] = {}
        for r in rows:
            out.setdefault(r.pack_id, []).append(r.item_id)
        return out

    @staticmethod
    def _pack(r: Any, members: list[UUID]) -> Pack:
        return Pack(
            id=r.id,
            key=r.key,
            title=r.title,
            style=r.style,
            item_ids=tuple(members),
            verses_amount=r.verses_amount,
            price_kopecks=r.price_kopecks,
            active=r.active,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )

    async def get(self, pack_id: UUID) -> Pack | None:
        """Пак по id."""
        row = (await self._s.execute(select(packs).where(packs.c.id == pack_id))).first()
        if row is None:
            return None
        return self._pack(row, (await self._members([pack_id])).get(pack_id, []))

    async def listing(self, *, active_only: bool) -> list[Pack]:
        """Паки, новые первыми."""
        stmt = select(packs)
        if active_only:
            stmt = stmt.where(packs.c.active.is_(True))
        rows = (
            await self._s.execute(stmt.order_by(packs.c.created_at.desc(), packs.c.id.desc()))
        ).all()
        members = await self._members([r.id for r in rows])
        return [self._pack(r, members.get(r.id, [])) for r in rows]

    async def add(self, pack: Pack) -> bool:
        """Создать пак; ``False`` - ключ занят."""
        row = (
            await self._s.execute(
                pg_insert(packs)
                .values(
                    id=pack.id,
                    key=pack.key,
                    title=pack.title,
                    style=pack.style,
                    verses_amount=pack.verses_amount,
                    price_kopecks=pack.price_kopecks,
                    active=pack.active,
                    created_at=pack.created_at,
                    updated_at=pack.updated_at,
                )
                .on_conflict_do_nothing(index_elements=[packs.c.key])
                .returning(packs.c.id)
            )
        ).first()
        if row is None:
            return False
        await self._put_members(pack)
        return True

    async def replace(self, pack: Pack) -> bool:
        """Заменить поля и состав; ``False`` - ключ занят другим паком."""
        clash = await self._s.scalar(
            select(packs.c.id).where(packs.c.key == pack.key, packs.c.id != pack.id)
        )
        if clash is not None:
            return False
        await self._s.execute(
            update(packs)
            .where(packs.c.id == pack.id)
            .values(
                key=pack.key,
                title=pack.title,
                style=pack.style,
                verses_amount=pack.verses_amount,
                price_kopecks=pack.price_kopecks,
                active=pack.active,
                updated_at=pack.updated_at,
            )
        )
        await self._s.execute(delete(pack_items).where(pack_items.c.pack_id == pack.id))
        await self._put_members(pack)
        return True

    async def _put_members(self, pack: Pack) -> None:
        await self._s.execute(
            pack_items.insert(),
            [
                {"pack_id": pack.id, "item_id": item_id, "position": pos}
                for pos, item_id in enumerate(pack.item_ids)
            ],
        )
