"""Схема БД rating (этап 4b, поток G; ADR 0037, 0039).

Рейтинг игрока — строка ``ratings`` на ``(user_id, ladder, season_id)``; новый сезон получает
копии строк прошлого с мягким сбросом (``event_version`` и число матчей калибровки
переезжают). ``rating_history`` — партиции по сезону (``PARTITION BY LIST (season_id)``):
партицию создаёт код вместе с сезоном, ``DEFAULT`` — страховка от потери строки.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
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
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata

LADDER_KEY = String(64)
HISTORY_PARTITION_PREFIX = "rating_history_"
"""Префикс партиций ``rating_history`` (``rating_history_s<hex сезона>``, ``..._default``)."""


def _ts(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


ladders = Table(
    "ladders",
    _md,
    Column("key", LADDER_KEY, primary_key=True),
    Column("domain", String(16), nullable=False),
    _ts("created_at"),
    CheckConstraint("domain IN ('music','film')", name="domain_known"),
)

ladder_versions = Table(
    "ladder_versions",
    _md,
    Column("ladder", LADDER_KEY, ForeignKey("ladders.key", ondelete="CASCADE"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("reason", String(500), nullable=False),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    _ts("published_at", nullable=True),
    PrimaryKeyConstraint("ladder", "version"),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("status IN ('draft','published','archived')", name="status_known"),
    # Не больше одного черновика и одной действующей версии на ladder — в схеме.
    Index(
        "uq_ladder_versions_draft",
        "ladder",
        unique=True,
        postgresql_where=text("status = 'draft'"),
    ),
    Index(
        "uq_ladder_versions_published",
        "ladder",
        unique=True,
        postgresql_where=text("status = 'published'"),
    ),
)

seasons = Table(
    "seasons",
    _md,
    Column("season_id", Uuid(as_uuid=True), primary_key=True),
    Column("ladder", LADDER_KEY, ForeignKey("ladders.key", ondelete="CASCADE"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("ladder_version", Integer, nullable=False),
    _ts("starts_at"),
    _ts("ends_at"),
    Column("status", String(16), nullable=False),
    _ts("finished_at", nullable=True),
    Column("closed_by", String(16), nullable=True),
    Column("players_count", Integer, nullable=True),
    UniqueConstraint("ladder", "number"),
    CheckConstraint("number >= 1", name="number_positive"),
    CheckConstraint("ends_at > starts_at", name="period_ordered"),
    CheckConstraint("status IN ('scheduled','active','finished')", name="status_known"),
    CheckConstraint(
        "closed_by IS NULL OR closed_by IN ('schedule','admin')", name="closed_by_known"
    ),
    Index("uq_seasons_active", "ladder", unique=True, postgresql_where=text("status = 'active'")),
    Index("ix_seasons_ladder_starts_at", "ladder", "starts_at"),
)

ratings = Table(
    "ratings",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("ladder", LADDER_KEY, nullable=False),
    Column("season_id", Uuid(as_uuid=True), ForeignKey("seasons.season_id"), nullable=False),
    Column("rating", Integer, nullable=False),
    # Матчи для калибровки: не обнуляются сбросом сезона (ADR 0039).
    Column("matches_total", Integer, nullable=False),
    Column("season_matches", Integer, nullable=False),
    # Монотонна по (user_id, ladder) через сезоны: строки нового сезона наследуют её.
    Column("event_version", Integer, nullable=False),
    _ts("updated_at"),
    PrimaryKeyConstraint("user_id", "ladder", "season_id"),
    CheckConstraint("season_matches >= 0 AND matches_total >= season_matches", name="matches_ok"),
    CheckConstraint("event_version >= 0", name="event_version_non_negative"),
    Index("ix_ratings_season_rating", "season_id", "rating"),
)

rating_history = Table(
    "rating_history",
    _md,
    Column("season_id", Uuid(as_uuid=True), nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    Column("ladder", LADDER_KEY, nullable=False),
    _ts("at"),
    Column("queue_format", String(16), nullable=False),
    Column("outcome", String(8), nullable=False),
    Column("old", Integer, nullable=False),
    Column("new", Integer, nullable=False),
    Column("delta", Integer, nullable=False),
    Column("old_tier", String(64), nullable=True),
    Column("tier", String(64), nullable=True),
    Column("calibrating", Boolean, nullable=False),
    Column("calibration_left", Integer, nullable=False),
    Column("matches_played", Integer, nullable=False),
    Column("leaver_penalty", Integer, nullable=False),
    Column("event_version", Integer, nullable=False),
    PrimaryKeyConstraint("season_id", "user_id", "match_id"),
    CheckConstraint("outcome IN ('win','loss','draw')", name="outcome_known"),
    CheckConstraint("leaver_penalty >= 0", name="penalty_non_negative"),
    Index("ix_rating_history_user", "user_id", "ladder", "at", "match_id"),
    Index("ix_rating_history_match", "match_id"),
    postgresql_partition_by="LIST (season_id)",
)

weekly_deltas = Table(
    "weekly_deltas",
    _md,
    Column("ladder", LADDER_KEY, nullable=False),
    Column("week_start", Date, nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("season_id", Uuid(as_uuid=True), nullable=False),
    Column("delta", Integer, nullable=False),
    _ts("updated_at"),
    PrimaryKeyConstraint("ladder", "week_start", "user_id"),
    Index("ix_weekly_deltas_top", "ladder", "week_start", "delta"),
)

weekly_tops = Table(
    "weekly_tops",
    _md,
    Column("ladder", LADDER_KEY, nullable=False),
    Column("week_start", Date, nullable=False),
    # null — неделя без положительных приростов: закрыта без события.
    Column("season_id", Uuid(as_uuid=True), nullable=True),
    Column("top", JSONB, nullable=False),
    _ts("finalized_at"),
    PrimaryKeyConstraint("ladder", "week_start"),
)

processed_matches = Table(
    "processed_matches",
    _md,
    Column("match_id", Uuid(as_uuid=True), primary_key=True),
    Column("ladder", LADDER_KEY, nullable=False),
    _ts("processed_at"),
)

users_proj = Table(
    "users_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("nickname", String(64), nullable=True),
    Column("deleted", Boolean, nullable=False, server_default=text("false")),
    Column("version", Integer, nullable=False),
)

rebuild_requests = Table(
    "rebuild_requests",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("ladder", LADDER_KEY, nullable=False),
    Column("season_id", Uuid(as_uuid=True), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("requested_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    _ts("done_at", nullable=True),
    # Причина, по которой запрос закрыт без пересборки (нет ladder-а/сезона и т. п.).
    Column("error", String(200), nullable=True),
    Index("ix_rebuild_requests_pending", "created_at", postgresql_where=text("done_at IS NULL")),
)

admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", String(200), nullable=False),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    _ts("created_at"),
    Index("ix_admin_actions_target_created_at", "target", "created_at"),
)

idempotency_keys = idempotency_table(_md)
