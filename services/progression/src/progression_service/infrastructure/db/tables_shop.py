"""Таблицы магазина, инвентаря, оформления, паков и промокодов (этап 5b, поток S1; ADR 0045-0048).

Инварианты держит схема, а не только код: у предмета есть условие открытия, активный предмет имеет
картинку, оформление ссылается на предмет из инвентаря игрока, счётчик активаций кода не выше
лимита, код хранится только как HMAC-хеш, ``(code_hash, user_id)`` - одно погашение кода игроком.
Суммы целые (``bigint``), время - ``timestamptz``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from progression_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata


def _ts(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


items = Table(
    "items",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("key", String(64), nullable=False),
    Column("slot", String(64), nullable=False),
    Column("rarity", String(64), nullable=False),
    Column("title", String(80), nullable=False),
    Column("media_kind", String(16), nullable=False),
    # Ключи объектов в S3 (`c/<32 hex>.webp|png`); NULL - картинка ещё не загружена.
    Column("asset_key", String(100), nullable=True),
    Column("poster_key", String(100), nullable=True),
    Column("level_required", Integer, nullable=True),
    Column("price", BigInteger, nullable=True),
    Column("active", Boolean, nullable=False, server_default=text("false")),
    _ts("created_at"),
    _ts("updated_at"),
    # Предмет только для награды сезонного события (5d, ADR 0056): не продаётся и не по уровню.
    Column("event_key", String(64), nullable=True),
    UniqueConstraint("key", name="uq_items_key"),
    CheckConstraint("media_kind IN ('static','animated')", name="media_kind_known"),
    CheckConstraint(
        "level_required IS NOT NULL OR price IS NOT NULL OR event_key IS NOT NULL",
        name="unlock_condition_required",
    ),
    CheckConstraint(
        "event_key IS NULL OR (level_required IS NULL AND price IS NULL)",
        name="event_item_not_sold",
    ),
    CheckConstraint(
        "level_required IS NULL OR level_required >= 1", name="level_required_positive"
    ),
    CheckConstraint("price IS NULL OR price >= 1", name="price_positive"),
    CheckConstraint("NOT active OR asset_key IS NOT NULL", name="active_has_media"),
    CheckConstraint("media_kind = 'animated' OR poster_key IS NULL", name="poster_only_animated"),
    Index("ix_items_slot_active", "slot", "active"),
    Index("ix_items_created", "created_at", "id"),
)

inventory = Table(
    "inventory",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column(
        "item_id", Uuid(as_uuid=True), ForeignKey("items.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("source", String(16), nullable=False),
    _ts("acquired_at"),
    # Поля 5d: в 5b всегда NULL (просроченные предметы скрывает чтение).
    _ts("expires_at", nullable=True),
    Column("convert_condition", String(200), nullable=True),
    PrimaryKeyConstraint("user_id", "item_id", name="pk_inventory"),
    CheckConstraint(
        "source IN ('purchase','level','wheel','promo','pack','admin','event')",
        name="source_known",
    ),
    Index("ix_inventory_item", "item_id"),
)

equipped = Table(
    "equipped",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("slot", String(64), nullable=False),
    Column("item_id", Uuid(as_uuid=True), nullable=False),
    _ts("equipped_at"),
    PrimaryKeyConstraint("user_id", "slot", name="pk_equipped"),
    # Надеть можно только то, что есть в инвентаре игрока.
    ForeignKeyConstraint(
        ["user_id", "item_id"],
        ["inventory.user_id", "inventory.item_id"],
        name="fk_equipped_inventory",
        ondelete="RESTRICT",
    ),
    # Индекс потомка под составной внешний ключ.
    Index("ix_equipped_user_item", "user_id", "item_id"),
)

packs = Table(
    "packs",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("key", String(64), nullable=False),
    Column("title", String(80), nullable=False),
    Column("style", String(64), nullable=False),
    Column("verses_amount", BigInteger, nullable=False, server_default=text("0")),
    Column("price_kopecks", BigInteger, nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("false")),
    _ts("created_at"),
    _ts("updated_at"),
    UniqueConstraint("key", name="uq_packs_key"),
    CheckConstraint("verses_amount >= 0", name="verses_non_negative"),
    CheckConstraint("price_kopecks >= 1", name="price_positive"),
)

pack_items = Table(
    "pack_items",
    _md,
    Column(
        "pack_id", Uuid(as_uuid=True), ForeignKey("packs.id", ondelete="RESTRICT"), nullable=False
    ),
    Column(
        "item_id", Uuid(as_uuid=True), ForeignKey("items.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("position", SmallInteger, nullable=False),
    PrimaryKeyConstraint("pack_id", "item_id", name="pk_pack_items"),
    CheckConstraint("position >= 0", name="position_non_negative"),
    Index("ix_pack_items_item", "item_id"),
)

promo_batches = Table(
    "promo_batches",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("label", String(80), nullable=False),
    Column("reward_kind", String(8), nullable=False),
    Column("verses_amount", BigInteger, nullable=True),
    Column(
        "item_id", Uuid(as_uuid=True), ForeignKey("items.id", ondelete="RESTRICT"), nullable=True
    ),
    Column("max_uses", Integer, nullable=False),
    Column("per_user_once", Boolean, nullable=False),
    _ts("expires_at", nullable=True),
    Column("codes_total", Integer, nullable=False),
    _ts("revoked_at", nullable=True),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    CheckConstraint("reward_kind IN ('verses','item')", name="reward_kind_known"),
    CheckConstraint(
        "(reward_kind = 'verses' AND verses_amount >= 1 AND item_id IS NULL)"
        " OR (reward_kind = 'item' AND verses_amount IS NULL AND item_id IS NOT NULL)",
        name="reward_matches_kind",
    ),
    CheckConstraint("max_uses >= 1", name="max_uses_positive"),
    CheckConstraint("codes_total >= 1", name="codes_total_positive"),
    Index("ix_promo_batches_created", "created_at", "id"),
    Index("ix_promo_batches_item", "item_id"),
)

promo_codes = Table(
    "promo_codes",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        Uuid(as_uuid=True),
        ForeignKey("promo_batches.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    # HMAC-SHA256 (hex) нормализованного кода серверным ключом; сам код не хранится.
    Column("code_hash", String(64), nullable=False),
    Column("max_uses", Integer, nullable=False),
    Column("activations", Integer, nullable=False, server_default=text("0")),
    _ts("created_at"),
    UniqueConstraint("code_hash", name="uq_promo_codes_hash"),
    # Цель составного внешнего ключа погашений (id, хеш и партия согласованы).
    UniqueConstraint("id", "code_hash", "batch_id", name="uq_promo_codes_id_hash_batch"),
    CheckConstraint("length(code_hash) = 64", name="code_hash_sha256"),
    CheckConstraint(
        "activations >= 0 AND activations <= max_uses", name="activations_within_limit"
    ),
    Index("ix_promo_codes_batch", "batch_id"),
)

promo_redemptions = Table(
    "promo_redemptions",
    _md,
    Column("code_hash", String(64), nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("code_id", Uuid(as_uuid=True), nullable=False),
    Column("batch_id", Uuid(as_uuid=True), nullable=False),
    _ts("redeemed_at"),
    PrimaryKeyConstraint("code_hash", "user_id", name="pk_promo_redemptions"),
    # Один составной ключ: id, хеш и партия погашения обязаны принадлежать одной строке кода.
    ForeignKeyConstraint(
        ["code_id", "code_hash", "batch_id"],
        ["promo_codes.id", "promo_codes.code_hash", "promo_codes.batch_id"],
        name="fk_promo_redemptions_code",
        ondelete="RESTRICT",
    ),
    Index("ix_promo_redemptions_batch_user", "batch_id", "user_id"),
    Index("ix_promo_redemptions_code", "code_id"),
)

SHOP_TABLES = (
    items,
    inventory,
    equipped,
    packs,
    pack_items,
    promo_batches,
    promo_codes,
    promo_redemptions,
)
