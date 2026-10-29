"""Схема БД media_pipeline. Новые таблицы объявлять на ``TABLES.metadata``."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata

# Проекция единиц каталога: статус и последняя применённая версия события.
# ``events_version`` — счётчик версий собственных событий AssetsReady/AssetsFailed по единице
# (aggregate_id = media_item_id): потребители сравнивают только version, поэтому она обязана
# расти по единице, а не по отдельному набору ассетов.
media_items_state = Table(
    "media_items_state",
    _md,
    Column("media_item_id", Uuid, primary_key=True),
    Column("domain", String(16), nullable=True),
    Column("kind", String(16), nullable=True),
    Column("status", String(16), nullable=False),
    Column("version", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("events_version", BigInteger, nullable=False, server_default=text("0")),
    CheckConstraint("status IN ('active', 'blocked')", name="status_valid"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("events_version >= 0", name="events_version_non_negative"),
)

source_media = Table(
    "source_media",
    _md,
    Column("id", Uuid, primary_key=True),
    Column(
        "media_item_id",
        Uuid,
        ForeignKey("media_items_state.media_item_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("kind", String(16), nullable=False),
    Column("origin", String(16), nullable=False),
    Column("uri", String(2000), nullable=True),
    Column("status", String(16), nullable=False),
    Column("object_key", String(80), nullable=True, unique=True),
    Column("sha256", String(64), nullable=True),
    Column("bytes", BigInteger, nullable=True),
    Column("mime", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # 0005 (этап 3a): порядок внутри вида, происхождение (D6), содержимое текстового исходника.
    Column("position", Integer, nullable=False, server_default=text("1")),
    Column("source_url", String(2000), nullable=True),
    Column("license_note", String(500), nullable=True),
    # none_as_null: Python None — SQL NULL, а не JSON null (CHECK text_content_kind).
    Column("text_content", JSONB(none_as_null=True), nullable=True),
    # 0007 (этап 3b, ADR 0029): владелец своих загадок (сборник); NULL — каталожный исходник.
    Column("scope_kind", String(16), nullable=True),
    Column("scope_id", Uuid, nullable=True),
    CheckConstraint(
        "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip', 'text')",
        name="kind_valid",
    ),
    CheckConstraint(
        "origin IN ('upload', 'tmdb', 'seed', 'catalog', 'collection')", name="origin_valid"
    ),
    CheckConstraint("status IN ('registered', 'ready', 'failed')", name="status_valid"),
    CheckConstraint(
        "status <> 'ready' OR (object_key IS NOT NULL AND sha256 IS NOT NULL AND bytes > 0) "
        "OR (kind = 'text' AND text_content IS NOT NULL)",
        name="ready_has_object",
    ),
    CheckConstraint("position BETWEEN 1 AND 100", name="position_range"),
    CheckConstraint("(kind = 'text') = (text_content IS NOT NULL)", name="text_content_kind"),
    CheckConstraint("(scope_kind IS NULL) = (scope_id IS NULL)", name="scope_pair"),
    CheckConstraint("scope_kind IN ('collection')", name="scope_kind_valid"),
    CheckConstraint("scope_kind IS NULL OR kind = 'text'", name="scope_text_only"),
    Index("ix_source_media_media_item_id", "media_item_id"),
    # Один текстовый исходник своих загадок на единицу у владельца; он же — поиск по scope.
    Index(
        "uq_source_media_scope_item",
        "scope_kind",
        "scope_id",
        "media_item_id",
        unique=True,
        postgresql_where=text("scope_id IS NOT NULL"),
    ),
)

asset_sets = Table(
    "asset_sets",
    _md,
    Column("id", Uuid, primary_key=True),
    Column(
        "media_item_id",
        Uuid,
        ForeignKey("media_items_state.media_item_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("clue_type", String(64), nullable=False),
    Column("profile_key", String(64), nullable=False),
    Column("params", JSONB, nullable=False),
    Column("params_hash", String(64), nullable=False),
    Column("levels_count", Integer, nullable=False),
    Column(
        "source_media_id",
        Uuid,
        ForeignKey("source_media.id", ondelete="SET NULL"),
        nullable=True,
    ),
    # 0005: все исходники набора по порядку (frame_sequence берёт N кадров); NULL — набор
    # до 0005, его единственный исходник — source_media_id.
    Column("source_media_ids", ARRAY(Uuid), nullable=True),
    Column("status", String(16), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("error_code", String(64), nullable=True),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # 0007 (ADR 0029): владелец набора (сборник); NULL — каталожный набор.
    Column("scope_kind", String(16), nullable=True),
    Column("scope_id", Uuid, nullable=True),
    CheckConstraint(
        "status IN ('pending', 'processing', 'ready', 'failed', 'disabled')", name="status_valid"
    ),
    CheckConstraint("levels_count BETWEEN 1 AND 20", name="levels_count_range"),
    CheckConstraint("generation >= 1", name="generation_positive"),
    CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("(scope_kind IS NULL) = (scope_id IS NULL)", name="scope_pair"),
    CheckConstraint("scope_kind IN ('collection')", name="scope_kind_valid"),
    # 0006: счётчики наборов профиля для GET /admin/v1/profiles.
    Index("ix_asset_sets_clue_type_params_hash", "clue_type", "params_hash"),
    # 0007: уникальность набора с учётом владельца — две частичные уникальности вместо
    # прежней UNIQUE (media_item_id, clue_type, profile_key, params_hash): у каталожных
    # наборов она та же, у наборов сборника — своя на каждый scope (и поиск по scope).
    Index(
        "uq_asset_sets_catalog_profile",
        "media_item_id",
        "clue_type",
        "profile_key",
        "params_hash",
        unique=True,
        postgresql_where=text("scope_id IS NULL"),
    ),
    Index(
        "uq_asset_sets_scope_profile",
        "scope_kind",
        "scope_id",
        "media_item_id",
        "clue_type",
        "profile_key",
        "params_hash",
        unique=True,
        postgresql_where=text("scope_id IS NOT NULL"),
    ),
    # Наборы единицы любого владельца (блокировка, admin-API): частичные индексы выше
    # такой запрос не покрывают.
    Index("ix_asset_sets_media_item_id", "media_item_id"),
)

asset_levels = Table(
    "asset_levels",
    _md,
    Column("asset_set_id", Uuid, ForeignKey("asset_sets.id", ondelete="CASCADE"), nullable=False),
    Column("level", Integer, nullable=False),
    Column("object_key", String(80), nullable=False, unique=True),
    Column("mime", String(64), nullable=False),
    Column("bytes", BigInteger, nullable=False),
    Column("duration_ms", Integer, nullable=True),
    Column("width", Integer, nullable=True),
    Column("height", Integer, nullable=True),
    PrimaryKeyConstraint("asset_set_id", "level"),
    CheckConstraint("level >= 1", name="level_positive"),
    CheckConstraint("bytes >= 0", name="bytes_non_negative"),
)

# Журнал выдачи ссылок (ADR 0012); строки старше retention удаляет IssueAssetLink.
# Субъект (сессия игры, матч) хранится только sha256-хэшем: журнал не должен содержать ПДн.
link_issuance = Table(
    "link_issuance",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("asset_set_id", Uuid, nullable=False),
    Column("level", Integer, nullable=False),
    Column("subject_hash", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    # Сервис-получатель ссылки (sub сервисного токена, D13); NULL — выдано до 0004.
    Column("client_id", String(64)),
    CheckConstraint("subject_hash ~ '^[0-9a-f]{64}$'", name="subject_hash_sha256"),
    CheckConstraint("client_id ~ '^[a-z][a-z0-9_]{0,63}$'", name="client_id_format"),
    Index("ix_link_issuance_issued_at", "issued_at"),
    Index("ix_link_issuance_asset_set_id", "asset_set_id"),
)

# Динамические профили ассетов (ADR 0025): заведены по game.ClueAssetsRequested.
asset_profiles_dynamic = Table(
    "asset_profiles_dynamic",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("clue_type", String(64), nullable=False),
    Column("domain", String(16), nullable=False),
    Column("params", JSONB, nullable=False),
    Column("params_hash", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("error_code", String(64), nullable=True),
    Column("request_id", Uuid, nullable=False),
    Column("mode_key", String(100), nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("backfill_cursor", Uuid, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("clue_type", "params_hash", "domain"),
    CheckConstraint("status IN ('backfilling', 'active', 'rejected')", name="status_valid"),
    CheckConstraint("domain IN ('music', 'film')", name="domain_valid"),
    CheckConstraint("params_hash ~ '^[0-9a-f]{64}$'", name="params_hash_sha256"),
    CheckConstraint("(status = 'rejected') = (error_code IS NOT NULL)", name="rejected_has_code"),
    Index("ix_asset_profiles_dynamic_status", "status"),
)

# Проекция сборников (ADR 0029): версия последнего применённого события collections и
# статус; удалённый сборник остаётся надгробием ``deleted``.
collections_state = Table(
    "collections_state",
    _md,
    Column("collection_id", Uuid, primary_key=True),
    Column("version", BigInteger, nullable=False),
    Column("status", String(16), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('published', 'hidden', 'deleted')", name="status_valid"),
    CheckConstraint("version >= 1", name="version_positive"),
)

# UGC-изображения (5b, ADR 0048): аватарки. ``version`` - число решений модератора.
# ``pending_key`` есть только у pending, ``public_key`` - только у approved.
ugc_images = Table(
    "ugc_images",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("owner_user_id", Uuid, nullable=False),
    Column("purpose", String(64), nullable=False),
    Column("status", String(16), nullable=False),
    Column("reason_code", String(64), nullable=True),
    Column("pending_key", String(64), nullable=True),
    Column("public_key", String(64), nullable=True),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("bytes", Integer, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("moderator_id", Uuid, nullable=True),
    Column("version", Integer, nullable=False, server_default=text("0")),
    CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
    CheckConstraint("version >= 0", name="version_non_negative"),
    CheckConstraint("width >= 1 AND height >= 1 AND bytes >= 1", name="dimensions_positive"),
    CheckConstraint(
        "(status = 'pending') = (pending_key IS NOT NULL)", name="pending_key_iff_pending"
    ),
    CheckConstraint(
        "(status = 'approved') = (public_key IS NOT NULL)", name="public_key_iff_approved"
    ),
    CheckConstraint(
        "pending_key IS NULL OR pending_key ~ '^p/[a-f0-9]{32}[.]webp$'", name="pending_key_shape"
    ),
    CheckConstraint(
        "public_key IS NULL OR public_key ~ '^u/[a-f0-9]{32}[.]webp$'", name="public_key_shape"
    ),
    UniqueConstraint("pending_key"),
    UniqueConstraint("public_key"),
    Index("ix_ugc_images_queue", "status", "submitted_at", "id"),
    Index("ix_ugc_images_owner_purpose_status", "owner_user_id", "purpose", "status"),
)

# Очередь гарантированного удаления объектов UGC: ключ пишется в транзакции решения, задача
# relay ``media.ugc.purge`` удаляет объект из S3 до успеха и стирает строку.
ugc_object_purge = Table(
    "ugc_object_purge",
    _md,
    Column("key", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("key ~ '^[pu]/[a-f0-9]{32}[.]webp$'", name="key_shape"),
    Index("ix_ugc_object_purge_created_at", "created_at"),
)

# Ключи идемпотентности admin-мутаций (gv_db.idempotency): ключ и ответ пишутся в той же
# транзакции, что и мутация.
idempotency_keys = idempotency_table(_md)

admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("actor_id", Uuid, nullable=False),
    Column("action", String(64), nullable=False),
    Column("target_id", Uuid, nullable=False),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False),
    Column("correlation_id", Uuid, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("length(reason) > 0", name="reason_not_empty"),
    Index("ix_admin_actions_target_id", "target_id"),
)

# Пакетная загрузка исходников архивом (ADR 0072 и план «пакеты»): строка на архив со
# счётчиками файлов и строки ошибок по файлам. Сам архив лежит в бакете исходников под
# ``archive_key`` до конца обработки.
import_packages = Table(
    "import_packages",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("actor_id", Uuid, nullable=False),
    Column("reason", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("archive_key", String(80), nullable=True),
    Column("archive_sha256", String(64), nullable=False),
    Column("archive_bytes", BigInteger, nullable=False),
    Column("total", Integer, nullable=False, server_default=text("0")),
    Column("done", Integer, nullable=False, server_default=text("0")),
    Column("failed", Integer, nullable=False, server_default=text("0")),
    Column("missing_source", Integer, nullable=False, server_default=text("0")),
    Column("missing_license", Integer, nullable=False, server_default=text("0")),
    Column("errors_dropped", Integer, nullable=False, server_default=text("0")),
    Column("error_code", String(64), nullable=True),
    Column("error_message", String(500), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("heartbeat_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "status IN ('queued', 'processing', 'done', 'partial', 'failed')", name="status_valid"
    ),
    CheckConstraint(
        "total >= 0 AND done >= 0 AND failed >= 0 AND done + failed <= total",
        name="counters_valid",
    ),
    CheckConstraint("archive_bytes >= 0", name="archive_bytes_non_negative"),
    Index("ix_import_packages_created_at", "created_at"),
)

import_package_errors = Table(
    "import_package_errors",
    _md,
    Column(
        "package_id",
        Uuid,
        ForeignKey("import_packages.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("seq", Integer, primary_key=True),
    Column("item_key", String(190), nullable=True),
    Column("path", String(300), nullable=True),
    Column("role", String(16), nullable=True),
    Column("code", String(64), nullable=False),
    Column("message", String(500), nullable=False),
)
