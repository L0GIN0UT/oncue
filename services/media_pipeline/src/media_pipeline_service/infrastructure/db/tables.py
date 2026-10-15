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
    CheckConstraint(
        "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip', 'text')",
        name="kind_valid",
    ),
    CheckConstraint("origin IN ('upload', 'tmdb', 'seed', 'catalog')", name="origin_valid"),
    CheckConstraint("status IN ('registered', 'ready', 'failed')", name="status_valid"),
    CheckConstraint(
        "status <> 'ready' OR (object_key IS NOT NULL AND sha256 IS NOT NULL AND bytes > 0) "
        "OR (kind = 'text' AND text_content IS NOT NULL)",
        name="ready_has_object",
    ),
    CheckConstraint("position BETWEEN 1 AND 100", name="position_range"),
    CheckConstraint("(kind = 'text') = (text_content IS NOT NULL)", name="text_content_kind"),
    Index("ix_source_media_media_item_id", "media_item_id"),
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
    UniqueConstraint("media_item_id", "clue_type", "profile_key", "params_hash"),
    CheckConstraint(
        "status IN ('pending', 'processing', 'ready', 'failed', 'disabled')", name="status_valid"
    ),
    CheckConstraint("levels_count BETWEEN 1 AND 20", name="levels_count_range"),
    CheckConstraint("generation >= 1", name="generation_positive"),
    CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    CheckConstraint("version >= 1", name="version_positive"),
    # 0006: счётчики наборов профиля для GET /admin/v1/profiles.
    Index("ix_asset_sets_clue_type_params_hash", "clue_type", "params_hash"),
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
