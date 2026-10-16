"""Схема БД game (план этапа 2, §5 «Таблицы»). Новые таблицы объявлять на ``TABLES.metadata``.

Инварианты — в схеме: статусы CHECK-ами, одна попытка дейлика на игрока — частичный
UNIQUE, единица не повторяется в наборе дня — UNIQUE, версии положительны.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
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

# ---------------------------------------------------------------- конфиги режимов
mode_configs = Table(
    "mode_configs",
    _md,
    Column("key", String(100), nullable=False),
    Column("version", Integer, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("body", JSONB, nullable=False),
    Column("body_hash", String(64), nullable=False),
    Column("actor_id", Uuid, nullable=True),
    Column("reason", Text, nullable=True),
    Column("rolled_back_from", Integer, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("key", "version"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("status IN ('published', 'archived')", name="status_valid"),
    CheckConstraint("domain IN ('music', 'film')", name="domain_valid"),
    CheckConstraint("length(body_hash) = 64", name="body_hash_sha256"),
)

mode_config_drafts = Table(
    "mode_config_drafts",
    _md,
    Column("key", String(100), primary_key=True),
    Column("body", JSONB, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("validation", JSONB, nullable=False),
    Column("updated_by", Uuid, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision >= 1", name="revision_positive"),
)

# ---------------------------------------------------------------- проекции
catalog_items = Table(
    "catalog_items",
    _md,
    Column("media_item_id", Uuid, primary_key=True),
    Column("domain", String(16), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("status", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("year", Integer, nullable=True),
    Column("aliases", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("title_aliases", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("persons", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("genres", ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")),
    Column("popularity", Float, nullable=True),
    # Этап 3a: метрики каталога для «больше/меньше» (D9; плюс ``year`` единицы) и персоны
    # для марафона и темы сетки (D14).
    Column("metrics", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("person_ids", ARRAY(Uuid), nullable=False, server_default=text("'{}'::uuid[]")),
    CheckConstraint("status IN ('active', 'blocked')", name="status_valid"),
    CheckConstraint("version >= 1", name="version_positive"),
    Index("ix_catalog_items_genres", "genres", postgresql_using="gin"),
    Index("ix_catalog_items_domain_kind", "domain", "kind"),
    Index("ix_catalog_items_person_ids", "person_ids", postgresql_using="gin"),
)

clue_assets = Table(
    "clue_assets",
    _md,
    Column("media_item_id", Uuid, nullable=False),
    Column("clue_type", String(64), nullable=False),
    Column("params_hash", String(64), nullable=False),
    Column("asset_set_id", Uuid, nullable=False),
    Column("levels_count", SmallInteger, nullable=False),
    Column("status", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    PrimaryKeyConstraint("media_item_id", "clue_type", "params_hash"),
    CheckConstraint("status IN ('ready', 'failed')", name="status_valid"),
    CheckConstraint("levels_count >= 1", name="levels_positive"),
    CheckConstraint("version >= 1", name="version_positive"),
    Index("ix_clue_assets_asset_set_id", "asset_set_id"),
)

# ---------------------------------------------------------------- наборы дня
daily_sets = Table(
    "daily_sets",
    _md,
    Column("mode_key", String(100), nullable=False),
    Column("day", Date, nullable=False),
    Column("mode_version", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("generation", Integer, nullable=False, server_default=text("1")),
    Column("generated_by", String(16), nullable=False),
    Column("locked_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # Этап 3a (ADR 0027): набор периода — ``day`` = начало периода; тема сетки — персона.
    Column("period", String(16), nullable=False, server_default=text("'day'")),
    Column("subject_id", Uuid, nullable=True),
    PrimaryKeyConstraint("mode_key", "day"),
    CheckConstraint("status IN ('scheduled', 'locked', 'failed')", name="status_valid"),
    CheckConstraint("period IN ('day', 'week')", name="period_valid"),
    CheckConstraint("generation >= 1", name="generation_positive"),
    CheckConstraint("status <> 'locked' OR locked_at IS NOT NULL", name="locked_has_time"),
)

daily_set_items = Table(
    "daily_set_items",
    _md,
    Column("mode_key", String(100), nullable=False),
    Column("day", Date, nullable=False),
    Column("idx", SmallInteger, nullable=False),
    Column("media_item_id", Uuid, nullable=False),
    PrimaryKeyConstraint("mode_key", "day", "idx"),
    ForeignKeyConstraint(
        ["mode_key", "day"], ["daily_sets.mode_key", "daily_sets.day"], ondelete="CASCADE"
    ),
    UniqueConstraint("mode_key", "day", "media_item_id"),
    CheckConstraint("idx >= 0", name="idx_non_negative"),
    # exclude_recent_days: единицы режима за последние N дней.
    Index("ix_daily_set_items_mode_key_media_item_id_day", "mode_key", "media_item_id", "day"),
)

# ---------------------------------------------------------------- сессии
game_sessions = Table(
    "game_sessions",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("player_kind", String(16), nullable=False),
    Column("player_id", Uuid, nullable=True),
    Column("mode_key", String(100), nullable=False),
    Column("mode_version", Integer, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("source_type", String(64), nullable=False),
    Column("clue_type", String(64), nullable=False),
    Column("day", Date, nullable=True),
    Column("preset_id", String(64), nullable=True),
    Column("seed", LargeBinary, nullable=False),
    Column("status", String(16), nullable=False),
    Column("score", Integer, nullable=False),
    Column("max_score", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("state", JSONB, nullable=False),
    Column("idle_timeout_s", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("last_move_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("player_kind IN ('user', 'guest')", name="player_kind_valid"),
    CheckConstraint("status IN ('active', 'finished', 'abandoned')", name="status_valid"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("score >= 0 AND max_score >= 0", name="score_non_negative"),
    CheckConstraint("idle_timeout_s > 0", name="idle_timeout_positive"),
    CheckConstraint("status = 'active' OR finished_at IS NOT NULL", name="finished_has_time"),
    # Одна попытка дейлика на игрока (гонка двух стартов решается здесь).
    Index(
        "uq_game_sessions_daily_attempt",
        "mode_key",
        "day",
        "player_kind",
        "player_id",
        unique=True,
        postgresql_where=text("day IS NOT NULL"),
    ),
    Index("ix_game_sessions_player", "player_kind", "player_id", text("started_at DESC")),
    Index(
        "ix_game_sessions_active_last_move",
        "last_move_at",
        postgresql_where=text("status = 'active'"),
    ),
)

daily_round_stats = Table(
    "daily_round_stats",
    _md,
    Column("mode_key", String(100), nullable=False),
    Column("day", Date, nullable=False),
    Column("idx", SmallInteger, nullable=False),
    Column("played", Integer, nullable=False, server_default=text("0")),
    Column("exact", Integer, nullable=False, server_default=text("0")),
    Column("partial", Integer, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("mode_key", "day", "idx"),
    CheckConstraint(
        "played >= 0 AND exact >= 0 AND partial >= 0 AND exact + partial <= played",
        name="counts_valid",
    ),
)

# ---------------------------------------------------------------- этап 3a: рекорды и сетка
player_records = Table(
    "player_records",
    _md,
    Column("user_id", Uuid, nullable=False),
    Column("mode_key", String(100), nullable=False),
    Column("best", Integer, nullable=False),
    Column("session_id", Uuid, nullable=False),
    Column("achieved_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("user_id", "mode_key"),
    CheckConstraint("best >= 0", name="best_non_negative"),
)

score_histogram = Table(
    "score_histogram",
    _md,
    Column("mode_key", String(100), nullable=False),
    Column("score", Integer, nullable=False),
    Column("sessions", Integer, nullable=False),
    PrimaryKeyConstraint("mode_key", "score"),
    CheckConstraint("score >= 0 AND sessions >= 0", name="counts_valid"),
)

bracket_pick_stats = Table(
    "bracket_pick_stats",
    _md,
    Column("mode_key", String(100), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("winner_id", Uuid, nullable=False),
    Column("loser_id", Uuid, nullable=False),
    Column("picks", Integer, nullable=False),
    PrimaryKeyConstraint("mode_key", "period_start", "winner_id", "loser_id"),
    CheckConstraint("picks >= 0", name="picks_non_negative"),
    CheckConstraint("winner_id <> loser_id", name="distinct_items"),
)

asset_requests = Table(
    "asset_requests",
    _md,
    Column("request_id", Uuid, primary_key=True),
    Column("mode_key", String(100), nullable=False),
    Column("draft_revision", Integer, nullable=False),
    Column("clue_type", String(64), nullable=False),
    Column("params_hash", String(64), nullable=False),
    Column("params", JSONB, nullable=False),
    Column("status", String(16), nullable=False),
    Column("actor_id", Uuid, nullable=False),
    Column("reason", Text, nullable=False),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('pending', 'sent')", name="status_valid"),
    CheckConstraint("length(params_hash) = 64", name="params_hash_sha256"),
    CheckConstraint("draft_revision >= 1", name="draft_revision_positive"),
    Index("ix_asset_requests_mode_key_requested_at", "mode_key", "requested_at"),
    # Повторный запрос того же набора параметров, пока ассетов нет, — без нового события.
    Index(
        "uq_asset_requests_pending_hash",
        "clue_type",
        "params_hash",
        unique=True,
        postgresql_where=text("status = 'pending'"),
    ),
)

# ---------------------------------------------------------------- admin
admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("actor_id", Uuid, nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", String(200), nullable=False),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_admin_actions_target_created_at", "target", "created_at"),
)

idempotency_keys = idempotency_table(_md)
