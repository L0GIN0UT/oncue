"""Таблицы друзей, блокировок, приватности, проекций и версий конфига (5c, поток F1).

Миграция ``0002_social_friends``. Инварианты - в схеме:

* ``friendships``: пара упорядочена (``user_a < user_b``), одна строка на пару;
* ``friend_requests``: не более одной ожидающей заявки на неупорядоченную пару
  (``user_a``/``user_b`` дублируют отправителя и адресата в порядке пары);
* ``friend_codes``: код уникален, у игрока один код;
* ``config_versions``: один черновик и одна действующая версия (частичные индексы).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import idempotency_table
from social_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata


def _ts(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


idempotency_keys = idempotency_table(_md)

config_versions = Table(
    "config_versions",
    _md,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("status", String(16), nullable=False),
    Column("body", JSONB, nullable=False),
    Column("body_sha256", String(64), nullable=False),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    _ts("published_at", nullable=True),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("status IN ('draft','published','archived')", name="status_known"),
    CheckConstraint("status <> 'published' OR published_at IS NOT NULL", name="published_dated"),
    CheckConstraint("length(body_sha256) = 64", name="body_sha256_len"),
    Index(
        "uq_config_versions_published",
        "status",
        unique=True,
        postgresql_where=text("status = 'published'"),
    ),
    Index(
        "uq_config_versions_draft",
        "status",
        unique=True,
        postgresql_where=text("status = 'draft'"),
    ),
)

friend_requests = Table(
    "friend_requests",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("from_user_id", Uuid(as_uuid=True), nullable=False),
    Column("to_user_id", Uuid(as_uuid=True), nullable=False),
    Column("user_a", Uuid(as_uuid=True), nullable=False),
    Column("user_b", Uuid(as_uuid=True), nullable=False),
    Column("status", String(16), nullable=False),
    _ts("created_at"),
    _ts("resolved_at", nullable=True),
    CheckConstraint("user_a < user_b", name="pair_ordered"),
    CheckConstraint("from_user_id <> to_user_id", name="not_self"),
    CheckConstraint("status IN ('pending','accepted','declined','cancelled')", name="status_known"),
    CheckConstraint(
        "(from_user_id = user_a AND to_user_id = user_b) "
        "OR (from_user_id = user_b AND to_user_id = user_a)",
        name="pair_matches_direction",
    ),
    Index(
        "uq_friend_requests_pending_pair",
        "user_a",
        "user_b",
        unique=True,
        postgresql_where=text("status = 'pending'"),
    ),
    Index("ix_friend_requests_from_created", "from_user_id", "created_at"),
    Index(
        "ix_friend_requests_to_pending",
        "to_user_id",
        postgresql_where=text("status = 'pending'"),
    ),
)

friendships = Table(
    "friendships",
    _md,
    Column("user_a", Uuid(as_uuid=True), nullable=False),
    Column("user_b", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    PrimaryKeyConstraint("user_a", "user_b"),
    CheckConstraint("user_a < user_b", name="pair_ordered"),
    Index("ix_friendships_user_b", "user_b"),
)

friend_codes = Table(
    "friend_codes",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("code", String(8), nullable=False, unique=True),
    _ts("rotated_at"),
    CheckConstraint("length(code) = 8", name="code_len"),
)

blocks = Table(
    "blocks",
    _md,
    Column("blocker_id", Uuid(as_uuid=True), nullable=False),
    Column("blocked_id", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    PrimaryKeyConstraint("blocker_id", "blocked_id"),
    CheckConstraint("blocker_id <> blocked_id", name="not_self"),
    Index("ix_blocks_blocked", "blocked_id"),
)

privacy_settings = Table(
    "privacy_settings",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("profile_public", Boolean, nullable=False, server_default=text("true")),
    Column("stats_public", Boolean, nullable=False, server_default=text("true")),
    Column("friend_requests", String(8), nullable=False, server_default=text("'all'")),
    _ts("updated_at"),
    CheckConstraint("friend_requests IN ('all','none')", name="friend_requests_known"),
)

users_proj = Table(
    "users_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("nickname", String(24), nullable=False),
    Column("nickname_key", String(24), nullable=False),
    Column("version", Integer, nullable=False),
    _ts("updated_at"),
    Index("ix_users_proj_nickname_key", "nickname_key"),
)

in_match_proj = Table(
    "in_match_proj",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    _ts("started_at"),
    PrimaryKeyConstraint("user_id", "match_id"),
    Index("ix_in_match_proj_match", "match_id"),
)
