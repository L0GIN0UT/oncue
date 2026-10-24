"""Схема БД match (R7, D8, data.md §4). Новые таблицы объявлять на ``TABLES.metadata``.

Postgres — история и проекции; горячее состояние комнат — Redis (ADR 0034):

* ``matches`` — матч (PK ``match_id`` = ``UNIQUE``: повтор итога после handoff — no-op);
* ``match_players`` — места и исходы игроков (``user_id`` обезличивается при удалении
  аккаунта); ``finished_at`` денормализован для keyset «Мои матчи»;
* ``match_rounds`` — раунды, партиционирование по месяцу ``played_at`` (партиции вперёд
  создаёт периодическая задача ``match.partitions.ensure``, остальное — в ``_default``);
* ``player_ratings_given`` — похвалы и жалобы (``PlayerRated``, один раз на пару и вид);
* ``users_proj`` — ники из identity; ``mode_heads`` — текущие версии онлайн-шаблонов game;
* ``ladders_proj`` / ``seasons_proj`` / ``ratings_proj`` — проекция ``rating.rating.v1``
  для подбора (ADR 0038):
  параметры очереди ladder-а и рейтинг игрока (без синхронных вызовов rating);
* ``admin_actions`` — аудит админских действий (принудительное завершение);
* ``idempotency_keys`` — ``Idempotency-Key`` оценок.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
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
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata

FINISHES = "('completed','forfeit','tech_win','aborted')"
PLAYER_FINISHES = "('completed','forfeit','left','tech_win','tech_loss')"


def _ts(name: str, *, nullable: bool = False) -> Column[datetime]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


matches = Table(
    "matches",
    _md,
    Column("match_id", Uuid(as_uuid=True), primary_key=True),
    Column("room_code", String(8), nullable=False),
    Column("mode_key", String(100), nullable=False),
    Column("mode_version", Integer, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("format", String(16), nullable=False),
    Column("ranked", Boolean, nullable=False, server_default=text("false")),
    Column("ladder", String(64), nullable=True),
    Column("meta", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "collection_ids",
        ARRAY(Uuid(as_uuid=True)),
        nullable=False,
        server_default=text("'{}'::uuid[]"),
    ),
    Column("players_count", SmallInteger, nullable=False),
    _ts("started_at"),
    _ts("finished_at", nullable=True),
    Column("finish", String(16), nullable=True),
    Column("abort_reason", String(64), nullable=True),
    _ts("created_at"),
    CheckConstraint("mode_version >= 1", name="mode_version_positive"),
    CheckConstraint("domain IN ('music','film')", name="domain_known"),
    CheckConstraint("format IN ('duel','party','lobby')", name="format_known"),
    CheckConstraint("players_count BETWEEN 2 AND 6", name="players_count_range"),
    CheckConstraint(f"finish IS NULL OR finish IN {FINISHES}", name="finish_known"),
    CheckConstraint("(finish IS NULL) = (finished_at IS NULL)", name="finish_with_time"),
    Index("ix_matches_finished_at", "finished_at"),
)

match_players = Table(
    "match_players",
    _md,
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    Column("slot", SmallInteger, nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=True),
    Column("score", Integer, nullable=False),
    Column("place", SmallInteger, nullable=False),
    Column("outcome", String(8), nullable=True),
    Column("finish", String(16), nullable=False),
    _ts("finished_at"),
    PrimaryKeyConstraint("match_id", "slot"),
    ForeignKeyConstraint(["match_id"], ["matches.match_id"], ondelete="CASCADE"),
    CheckConstraint("slot BETWEEN 0 AND 5", name="slot_range"),
    CheckConstraint("score >= 0", name="score_non_negative"),
    CheckConstraint("place BETWEEN 1 AND 6", name="place_range"),
    CheckConstraint("outcome IS NULL OR outcome IN ('win','loss','draw')", name="outcome_known"),
    CheckConstraint(f"finish IN {PLAYER_FINISHES}", name="finish_known"),
    Index(
        "ix_match_players_user_finished",
        "user_id",
        text("finished_at DESC"),
        text("match_id DESC"),
        postgresql_where=text("user_id IS NOT NULL"),
    ),
)

match_rounds = Table(
    "match_rounds",
    _md,
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    Column("round_index", SmallInteger, nullable=False),
    _ts("played_at"),
    Column("media_item_id", Uuid(as_uuid=True), nullable=False),
    Column("void", Boolean, nullable=False, server_default=text("false")),
    Column("results", JSONB, nullable=False),
    PrimaryKeyConstraint("match_id", "round_index", "played_at"),
    CheckConstraint("round_index BETWEEN 0 AND 49", name="round_index_range"),
    postgresql_partition_by="RANGE (played_at)",
)

player_ratings_given = Table(
    "player_ratings_given",
    _md,
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    Column("from_id", Uuid(as_uuid=True), nullable=False),
    Column("to_id", Uuid(as_uuid=True), nullable=False),
    Column("kind", String(8), nullable=False),
    Column("reason", String(64), nullable=True),
    _ts("created_at"),
    PrimaryKeyConstraint("match_id", "from_id", "to_id", "kind"),
    ForeignKeyConstraint(["match_id"], ["matches.match_id"], ondelete="CASCADE"),
    CheckConstraint("from_id <> to_id", name="not_self"),
    CheckConstraint("kind IN ('praise','report')", name="kind_known"),
    Index("ix_player_ratings_given_to", "to_id", "created_at"),
    Index("ix_player_ratings_given_from", "from_id"),
)

users_proj = Table(
    "users_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("nickname", String(64), nullable=True),
    Column("version", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint("version >= 1", name="version_positive"),
)

mode_heads = Table(
    "mode_heads",
    _md,
    Column("mode_key", String(100), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("archived", Boolean, nullable=False, server_default=text("false")),
    _ts("updated_at"),
    CheckConstraint("version >= 1", name="version_positive"),
)

ladders_proj = Table(
    "ladders_proj",
    _md,
    Column("ladder", String(64), primary_key=True),
    Column("version", Integer, nullable=False),
    Column("domain", String(16), nullable=False),
    Column("formats", JSONB, nullable=False),
    Column("matchmaking", JSONB, nullable=False),
    _ts("updated_at"),
    CheckConstraint("version >= 1", name="version_positive"),
)

seasons_proj = Table(
    "seasons_proj",
    _md,
    Column("ladder", String(64), primary_key=True),
    Column("season_id", Uuid(as_uuid=True), nullable=False),
    _ts("starts_at"),
    Column("soft_reset", JSONB, nullable=True),
    _ts("updated_at"),
)

ratings_proj = Table(
    "ratings_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("ladder", String(64), nullable=False),
    Column("rating", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("season_id", Uuid(as_uuid=True), nullable=True),
    _ts("updated_at"),
    PrimaryKeyConstraint("user_id", "ladder"),
    CheckConstraint("version >= 0", name="version_not_negative"),
)

# Санкции игрока из ``social.UserSanctionsChanged`` (ADR 0052, миграция 0004): по ``active``
# маска считается на момент запроса (истёкшие записи не входят); версия монотонна.
sanctions_proj = Table(
    "sanctions_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("mask", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    _ts("next_expiry_at", nullable=True),
    Column("active", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    _ts("updated_at"),
    CheckConstraint("version >= 1", name="sanctions_version_positive"),
    CheckConstraint("mask BETWEEN 0 AND 65535", name="sanctions_mask_range"),
)

admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("room_code", String(8), nullable=False),
    Column("match_id", Uuid(as_uuid=True), nullable=True),
    Column("reason", String(500), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    _ts("created_at"),
    UniqueConstraint("actor_id", "idempotency_key"),
    Index("ix_admin_actions_room", "room_code", "created_at"),
)

idempotency_keys = idempotency_table(_md)

ROUND_PARTITION_PREFIX = "match_rounds_"
"""Префикс партиций ``match_rounds`` (``match_rounds_y2026m10``, ``match_rounds_default``)."""
