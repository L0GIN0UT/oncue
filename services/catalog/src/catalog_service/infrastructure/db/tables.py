"""Схема БД catalog. Новые таблицы объявлять на ``TABLES.metadata``.

Инварианты продублированы в схеме (CHECK, UNIQUE, FK), а не только в домене. Перечисления —
строками с CHECK, а не Postgres ENUM (расширяемость без блокирующих миграций).
Расширение ``pg_trgm`` создаёт init-скрипт БД (у роли сервиса нет прав суперпользователя).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Column,
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
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata


def _fk_item() -> ForeignKey:
    return ForeignKey("media_items.id", ondelete="CASCADE")


def _ts(name: str) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=False, server_default=func.now())


media_items = Table(
    "media_items",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("domain", String(16), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("title", String(300), nullable=False),
    Column("original_title", String(300), nullable=True),
    Column("year", Integer, nullable=True),
    Column("subtitle", String(300), nullable=True),
    Column("popularity", Float, nullable=False, server_default=text("0")),
    Column("blocked_reason_code", String(64), nullable=True),
    Column("version", Integer, nullable=False),
    # 0004 (этап 3a): страны производства и одобренные тексты загадок (ADR 0024).
    Column("countries", ARRAY(String(2)), nullable=False, server_default=text("'{}'")),
    Column("clue_texts", JSONB(none_as_null=True), nullable=True),
    _ts("created_at"),
    _ts("updated_at"),
    CheckConstraint("domain IN ('music', 'film')", name="domain_known"),
    CheckConstraint("kind IN ('track', 'film')", name="kind_known"),
    CheckConstraint(
        "(kind = 'track' AND domain = 'music') OR (kind = 'film' AND domain = 'film')",
        name="kind_matches_domain",
    ),
    CheckConstraint("status IN ('active', 'blocked')", name="status_known"),
    CheckConstraint("year IS NULL OR year BETWEEN 1870 AND 2100", name="year_range"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint(
        "(status = 'blocked') = (blocked_reason_code IS NOT NULL)", name="blocked_has_reason"
    ),
)

aliases = Table(
    "aliases",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), nullable=False),
    Column("normalized", String(300), nullable=False),
    Column("text", String(300), nullable=False),
    Column("lang", String(8), nullable=True),
    Column("kind", String(16), nullable=False),
    Column("source", String(16), nullable=False),
    Column("position", SmallInteger, nullable=False),
    PrimaryKeyConstraint("media_item_id", "normalized"),
    CheckConstraint("kind IN ('title', 'alt', 'translit', 'short')", name="kind_known"),
    CheckConstraint("source IN ('import', 'manual')", name="source_known"),
    CheckConstraint("normalized <> ''", name="normalized_not_empty"),
    # Нечёткий поиск и префиксы (LIKE 'q%') — pg_trgm; fallback автокомплита без OpenSearch.
    Index(
        "ix_aliases_normalized_trgm",
        "normalized",
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    ),
)

persons = Table(
    "persons",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("external_key", String(200), nullable=False, unique=True),
    Column("name", String(200), nullable=False),
    _ts("created_at"),
)

media_item_persons = Table(
    "media_item_persons",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), nullable=False),
    Column("person_id", Uuid(as_uuid=True), ForeignKey("persons.id"), nullable=False),
    Column("role", String(16), nullable=False),
    Column("position", Integer, nullable=False),
    PrimaryKeyConstraint("media_item_id", "person_id", "role"),
    CheckConstraint("role IN ('artist', 'director', 'actor')", name="role_known"),
    CheckConstraint("position >= 0", name="position_non_negative"),
    Index("ix_media_item_persons_person_id", "person_id"),
)

genres = Table(
    "genres",
    _md,
    Column("slug", String(64), primary_key=True),
)

media_item_genres = Table(
    "media_item_genres",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), nullable=False),
    Column("genre_slug", String(64), ForeignKey("genres.slug"), nullable=False),
    Column("position", SmallInteger, nullable=False),
    PrimaryKeyConstraint("media_item_id", "genre_slug"),
)

external_refs = Table(
    "external_refs",
    _md,
    Column("source", String(32), nullable=False),
    Column("external_id", String(200), nullable=False),
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), nullable=False),
    # Один внешний объект — одна единица каталога (повторный импорт обновляет её).
    PrimaryKeyConstraint("source", "external_id"),
    CheckConstraint(
        "source IN ('tmdb', 'musicbrainz', 'isrc', 'spotify', 'yandex_music', 'seed')",
        name="source_known",
    ),
    Index("ix_external_refs_media_item_id", "media_item_id"),
)

media_metrics = Table(
    "media_metrics",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), primary_key=True),
    Column("data", JSONB, nullable=False),
)

source_media_refs = Table(
    "source_media_refs",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("uri", String(2000), nullable=False),
    # Порядок строки в снимке единицы (не путать с kind_position).
    Column("position", SmallInteger, nullable=False),
    # 0004: порядок среди исходников своего вида (контракт source_media[].position) и
    # происхождение (D6 плана этапа 3a).
    Column("kind_position", SmallInteger, nullable=False, server_default=text("1")),
    Column("source_url", String(2000), nullable=True),
    Column("license_note", String(500), nullable=True),
    CheckConstraint(
        "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip')", name="kind_known"
    ),
    CheckConstraint("origin IN ('upload', 'tmdb')", name="origin_known"),
    CheckConstraint("kind_position BETWEEN 1 AND 100", name="kind_position_range"),
    Index("ix_source_media_refs_media_item_id", "media_item_id"),
)

# Тексты загадок (ADR 0024): черновик и ревизии; одобренный снимок — media_items.clue_texts.
clue_texts = Table(
    "clue_texts",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), primary_key=True),
    Column("plot_raw", Text, nullable=True),
    Column("plot_masked", String(2000), nullable=True),
    Column("tagline", String(300), nullable=True),
    Column("emoji", String(64), nullable=True),
    Column("status", String(16), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("approved_revision", Integer, nullable=True),
    _ts("updated_at"),
    CheckConstraint("status IN ('draft', 'approved')", name="status_known"),
    CheckConstraint("revision >= 0", name="revision_non_negative"),
    CheckConstraint(
        "approved_revision IS NULL OR approved_revision BETWEEN 1 AND revision",
        name="approved_revision_range",
    ),
    CheckConstraint("plot_raw IS NULL OR length(plot_raw) <= 5000", name="plot_raw_length"),
)

# Задания переопубликации единиц (RepublishItems): курсор порций и счётчик.
republish_jobs = Table(
    "republish_jobs",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("domain", String(16), nullable=True),
    Column("status", String(16), nullable=False),
    Column("cursor", Uuid(as_uuid=True), nullable=True),
    Column("published", Integer, nullable=False, server_default=text("0")),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("reason", String(500), nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
    CheckConstraint("domain IS NULL OR domain IN ('music', 'film')", name="domain_known"),
    CheckConstraint("status IN ('running', 'done')", name="status_known"),
    CheckConstraint("published >= 0", name="published_non_negative"),
)

media_item_clue_status = Table(
    "media_item_clue_status",
    _md,
    Column("media_item_id", Uuid(as_uuid=True), _fk_item(), nullable=False),
    Column("clue_type", String(64), nullable=False),
    Column("profile_key", String(64), nullable=False),
    Column("asset_set_id", Uuid(as_uuid=True), nullable=False),
    Column("status", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("levels_count", Integer, nullable=True),
    Column("error_code", String(64), nullable=True),
    _ts("updated_at"),
    PrimaryKeyConstraint("media_item_id", "clue_type", "profile_key"),
    CheckConstraint("status IN ('ready', 'failed')", name="status_known"),
    CheckConstraint("version >= 1", name="version_positive"),
)

import_jobs = Table(
    "import_jobs",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("source", String(32), nullable=False),
    Column("ref", String(200), nullable=True),
    Column("file_id", String(100), nullable=True),
    Column("status", String(16), nullable=False),
    Column("counters", JSONB, nullable=False),
    Column("actor_id", Uuid(as_uuid=True), nullable=True),
    Column("reason", String(500), nullable=False),
    Column("idempotency_key", String(128), nullable=True),
    Column("error", String(500), nullable=True),
    _ts("created_at"),
    _ts("updated_at"),
    UniqueConstraint("actor_id", "idempotency_key"),
    CheckConstraint(
        "source IN ('tmdb', 'file', 'musicbrainz', 'spotify', 'yandex_music')",
        name="source_known",
    ),
    CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name="status_known"),
    CheckConstraint("(ref IS NULL) <> (file_id IS NULL)", name="ref_xor_file"),
)

# Аудит админ-мутаций. Ответы для повторов по Idempotency-Key — в ``idempotency_keys``;
# ``request_hash``/``response`` здесь остаются как часть аудита.
admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target_id", Uuid(as_uuid=True), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("response", JSONB, nullable=False),
    _ts("created_at"),
    UniqueConstraint("actor_id", "idempotency_key"),
    Index("ix_admin_actions_target_id", "target_id"),
)

idempotency_keys = idempotency_table(_md)
"""Ключи идемпотентности admin-мутаций (``gv_db.idempotency``)."""
