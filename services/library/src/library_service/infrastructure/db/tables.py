"""Схема БД library (план этапа 3b §6, L1). Новые таблицы объявлять на ``TABLES.metadata``.

Инварианты — в схеме: вид полки и её ключ/название согласованы CHECK-ом, системная полка
одна на ключ (UNIQUE ``(user_id, key)``), единица на полке одна (PK), счётчики и версии
неотрицательны. Строки единиц удаляются вместе со своей полкой (FK ``ON DELETE CASCADE``).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata

_DOMAIN_CHECK = "domain IN ('music', 'film')"
_SOURCES = "'reveal', 'results', 'card', 'collection', 'library'"

shelves = Table(
    "shelves",
    _md,
    Column("id", Uuid, nullable=False),
    Column("user_id", Uuid, nullable=False),
    Column("kind", String(16), nullable=False),
    Column("key", String(64), nullable=True),
    Column("title", Text, nullable=True),
    Column("items_count", Integer, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("id"),
    # NULL-ключи своих полок различны: UNIQUE ограничивает только системные.
    UniqueConstraint("user_id", "key"),
    CheckConstraint(
        "(kind = 'system' AND key IS NOT NULL AND title IS NULL)"
        " OR (kind = 'custom' AND key IS NULL AND title IS NOT NULL)",
        name="kind_fields",
    ),
    CheckConstraint("items_count >= 0", name="items_count_non_negative"),
)

shelf_items = Table(
    "shelf_items",
    _md,
    Column("shelf_id", Uuid, nullable=False),
    Column("media_item_id", Uuid, nullable=False),
    Column("user_id", Uuid, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("source", String(16), nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("shelf_id", "media_item_id"),
    ForeignKeyConstraint(["shelf_id"], ["shelves.id"], ondelete="CASCADE"),
    CheckConstraint(_DOMAIN_CHECK, name="domain_valid"),
    CheckConstraint(f"source IN ({_SOURCES})", name="source_valid"),
    # «На каких полках единица» и удаление пользователя порциями.
    Index("ix_shelf_items_user_id_media_item_id", "user_id", "media_item_id"),
    # Страница полки: новые сверху, keyset по (added_at, media_item_id).
    Index("ix_shelf_items_shelf_id_added_at", "shelf_id", "added_at", "media_item_id"),
)

catalog_items_proj = Table(
    "catalog_items_proj",
    _md,
    Column("media_item_id", Uuid, nullable=False),
    Column("version", Integer, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("title", Text, nullable=False),
    Column("year", Integer, nullable=True),
    Column("persons", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("media_item_id"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint(_DOMAIN_CHECK, name="domain_valid"),
    CheckConstraint("status IN ('active', 'blocked')", name="status_valid"),
)

user_versions = Table(
    "user_versions",
    _md,
    Column("user_id", Uuid, nullable=False),
    Column("version", BigInteger, nullable=False, server_default=text("0")),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("user_id"),
    CheckConstraint("version >= 0", name="version_non_negative"),
)

user_purges = Table(
    "user_purges",
    _md,
    Column("user_id", Uuid, nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("user_id"),
    Index("ix_user_purges_requested_at", "requested_at"),
)

idempotency_keys = idempotency_table(_md)
