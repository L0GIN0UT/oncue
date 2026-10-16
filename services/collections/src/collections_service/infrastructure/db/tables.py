"""Схема БД collections (план этапа 3b §5 K1, ADR 0028, 0030).

* ``collections`` — агрегат и денормализованные поля текущей версии для каталога
  (``title_tsv`` — tsvector ``russian`` + ``simple`` по названию, описанию и словам тегов;
  GIN + ``pg_trgm`` по названию), счётчики и сложность;
* ``collection_versions`` — неизменяемые версии состава; ``collection_items`` — текущий
  опубликованный состав (поиск сборников по единице при блокировке, карточка);
* ``collection_drafts`` — один черновик на сборник (``revision``);
* ``likes``/``favorites`` (PK ``(user_id, collection_id)``), ``completions`` (первое
  завершение на версию), ``play_stats`` (дни для «популярных»), ``author_restrictions``;
* проекции ``catalog_items_proj`` и ``users_proj``; ``admin_actions``; ``idempotency_keys``.

Расширение ``pg_trgm`` создаёт init-скрипт БД (у роли сервиса нет прав суперпользователя).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ColumnElement,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    Text,
    Uuid,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

from gv_db import create_metadata, idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata

CATALOG_WHERE = text("status = 'published' AND visibility = 'public'")
"""Условие публичного каталога (частичные индексы сортировок)."""
DIFFICULTY_SORT: ColumnElement[float] = func.coalesce(
    literal_column("difficulty_score"), literal_column("'-1'::double precision")
)
"""Сортировка «сложные раньше»: без сложности — в конце (то же выражение — в запросе)."""


def _ts(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


collections = Table(
    "collections",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("domain", String(16), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("visibility", String(16), nullable=False, server_default=text("'public'")),
    Column("author_id", Uuid(as_uuid=True), nullable=True),
    Column("version", Integer, nullable=False, server_default=text("0")),
    Column("content_version", Integer, nullable=False, server_default=text("0")),
    Column("draft_seq", Integer, nullable=False, server_default=text("0")),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=True),
    Column("tags", ARRAY(String(32)), nullable=False, server_default=text("'{}'")),
    Column("cover_preset", String(32), nullable=True),
    Column("default_mode_key", String(100), nullable=True),
    Column("items_count", SmallInteger, nullable=False, server_default=text("0")),
    Column("title_tsv", TSVECTOR, nullable=True),
    Column("likes_count", Integer, nullable=False, server_default=text("0")),
    Column("plays_count", Integer, nullable=False, server_default=text("0")),
    Column("ratio_sum", Float, nullable=False, server_default=text("0")),
    Column("ratio_count", Integer, nullable=False, server_default=text("0")),
    Column("difficulty_score", Float, nullable=True),
    Column("popular_score", Float, nullable=False, server_default=text("0")),
    Column("featured", Boolean, nullable=False, server_default=text("false")),
    Column("featured_rank", Integer, nullable=True),
    Column("hidden_reason", String(500), nullable=True),
    Column("hidden_by", Uuid(as_uuid=True), nullable=True),
    _ts("hidden_at", nullable=True),
    _ts("created_at"),
    _ts("updated_at"),
    _ts("first_published_at", nullable=True),
    _ts("published_at", nullable=True),
    _ts("deleted_at", nullable=True),
    CheckConstraint("domain IN ('music', 'film')", name="domain_known"),
    CheckConstraint("kind IN ('ugc', 'curated')", name="kind_known"),
    CheckConstraint("status IN ('draft', 'published', 'hidden', 'deleted')", name="status_known"),
    CheckConstraint("visibility IN ('public', 'unlisted')", name="visibility_known"),
    # Кураторский — без автора и сразу опубликован; пользовательский черновик — с автором
    # (обезличивание при UserDeleted оставляет только опубликованные версии).
    CheckConstraint(
        "kind = 'ugc' OR (author_id IS NULL AND status <> 'draft')", name="curated_consistent"
    ),
    CheckConstraint(
        "kind = 'curated' OR status <> 'draft' OR author_id IS NOT NULL", name="draft_has_author"
    ),
    CheckConstraint("status <> 'draft' OR content_version = 0", name="draft_unpublished"),
    CheckConstraint(
        "status NOT IN ('published', 'hidden') OR (content_version >= 1 "
        "AND first_published_at IS NOT NULL AND published_at IS NOT NULL "
        "AND default_mode_key IS NOT NULL)",
        name="published_complete",
    ),
    CheckConstraint("status <> 'hidden' OR hidden_at IS NOT NULL", name="hidden_has_reason"),
    CheckConstraint("status <> 'deleted' OR deleted_at IS NOT NULL", name="deleted_has_time"),
    CheckConstraint("NOT featured OR status = 'published'", name="featured_published"),
    CheckConstraint(
        "version >= 0 AND content_version >= 0 AND draft_seq >= 0", name="versions_non_negative"
    ),
    CheckConstraint(
        "likes_count >= 0 AND plays_count >= 0 AND ratio_count >= 0 AND ratio_sum >= 0",
        name="counters_non_negative",
    ),
    CheckConstraint("items_count BETWEEN 0 AND 200", name="items_count_range"),
    CheckConstraint(
        "difficulty_score IS NULL OR difficulty_score BETWEEN 0 AND 1", name="difficulty_range"
    ),
    CheckConstraint("title <> ''", name="title_not_empty"),
    Index("ix_collections_title_tsv", "title_tsv", postgresql_using="gin"),
    Index(
        "ix_collections_title_trgm",
        "title",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    ),
    Index("ix_collections_author_updated", "author_id", "updated_at", "id"),
    Index("ix_collections_updated", "updated_at", "id"),
    Index(
        "ix_collections_catalog_popular",
        "popular_score",
        "id",
        postgresql_where=CATALOG_WHERE,
    ),
    Index(
        "ix_collections_catalog_new",
        "first_published_at",
        "id",
        postgresql_where=CATALOG_WHERE,
    ),
    Index(
        "ix_collections_catalog_likes",
        "likes_count",
        "id",
        postgresql_where=CATALOG_WHERE,
    ),
    Index(
        "ix_collections_catalog_difficulty",
        DIFFICULTY_SORT.desc(),
        text("id DESC"),
        postgresql_where=CATALOG_WHERE,
    ),
    Index(
        "ix_collections_catalog_featured",
        "featured_rank",
        "id",
        postgresql_where=text("featured AND status = 'published' AND visibility = 'public'"),
    ),
)

collection_versions = Table(
    "collection_versions",
    _md,
    Column(
        "collection_id",
        Uuid(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("content_version", Integer, nullable=False),
    Column("body", JSONB, nullable=False),
    _ts("published_at"),
    PrimaryKeyConstraint("collection_id", "content_version"),
    CheckConstraint("content_version >= 1", name="content_version_positive"),
)

collection_items = Table(
    "collection_items",
    _md,
    Column(
        "collection_id",
        Uuid(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("position", SmallInteger, nullable=False),
    Column("media_item_id", Uuid(as_uuid=True), nullable=False),
    Column("emoji", String(128), nullable=True),
    PrimaryKeyConstraint("collection_id", "position"),
    CheckConstraint("position BETWEEN 0 AND 199", name="position_range"),
    Index("ix_collection_items_media_item", "media_item_id", "collection_id"),
)

collection_drafts = Table(
    "collection_drafts",
    _md,
    Column(
        "collection_id",
        Uuid(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("revision", Integer, nullable=False),
    Column("base_version", Integer, nullable=True),
    Column("body", JSONB, nullable=False),
    Column("items_count", SmallInteger, nullable=False, server_default=text("0")),
    Column("client_label", String(64), nullable=True),
    _ts("updated_at"),
    CheckConstraint("revision >= 1", name="revision_positive"),
)


def _reaction(name: str) -> Table:
    return Table(
        name,
        _md,
        Column("user_id", Uuid(as_uuid=True), nullable=False),
        Column(
            "collection_id",
            Uuid(as_uuid=True),
            ForeignKey("collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _ts("created_at"),
        PrimaryKeyConstraint("user_id", "collection_id"),
        Index(f"ix_{name}_collection", "collection_id"),
        Index(f"ix_{name}_user_created", "user_id", "created_at", "collection_id"),
    )


likes = _reaction("likes")
favorites = _reaction("favorites")

completions = Table(
    "completions",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column(
        "collection_id",
        Uuid(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("content_version", Integer, nullable=False),
    Column("ratio", Float, nullable=True),
    Column("session_id", Uuid(as_uuid=True), nullable=False),
    _ts("first_at"),
    PrimaryKeyConstraint("user_id", "collection_id", "content_version"),
    CheckConstraint("ratio IS NULL OR ratio BETWEEN 0 AND 1", name="ratio_range"),
    CheckConstraint("content_version >= 1", name="content_version_positive"),
    Index("ix_completions_collection", "collection_id"),
)

play_stats = Table(
    "play_stats",
    _md,
    Column(
        "collection_id",
        Uuid(as_uuid=True),
        ForeignKey("collections.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("day", Date, nullable=False),
    Column("likes", Integer, nullable=False, server_default=text("0")),
    Column("plays", Integer, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("collection_id", "day"),
    CheckConstraint("likes >= 0 AND plays >= 0", name="counters_non_negative"),
    Index("ix_play_stats_day", "day"),
)

author_restrictions = Table(
    "author_restrictions",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("reason", String(500), nullable=False),
    Column("actor_id", Uuid(as_uuid=True), nullable=True),
    _ts("created_at"),
)

catalog_items_proj = Table(
    "catalog_items_proj",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), primary_key=True),
    Column("domain", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("title", String(500), nullable=False),
    Column("year", SmallInteger, nullable=True),
    CheckConstraint("status IN ('active', 'blocked')", name="status_known"),
    CheckConstraint("version >= 1", name="version_positive"),
    Index(
        "ix_catalog_items_proj_blocked",
        "media_item_id",
        postgresql_where=text("status = 'blocked'"),
    ),
)

users_proj = Table(
    "users_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("nickname", String(64), nullable=True),
    Column("version", Integer, nullable=False),
    CheckConstraint("version >= 1", name="version_positive"),
)

admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", String(64), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at"),
    Index("ix_admin_actions_target", "target", "created_at"),
)

idempotency_keys = idempotency_table(_md)
