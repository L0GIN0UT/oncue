"""Схема БД identity. Новые таблицы объявлять на ``TABLES.metadata``.

Инварианты закреплены в схеме: уникальность ника/e-mail без учёта регистра (``citext``),
аккаунт провайдера привязан максимум к одному пользователю, токены хранятся только хэшами.
"""

from __future__ import annotations

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import CITEXT, JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata
_TS = DateTime(timezone=True)

users = Table(
    "users",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("nickname", CITEXT, nullable=False, unique=True),
    Column("email", CITEXT, nullable=False, unique=True),
    Column("email_verified_at", _TS, nullable=True),
    Column("display_name", String(40), nullable=False),
    Column("locale", String(16), nullable=True),
    Column("status", String(16), nullable=False),
    Column("roles_version", Integer, nullable=False, server_default=text("0")),
    Column("version", Integer, nullable=False),
    Column("created_at", _TS, nullable=False),
    Column("deleted_at", _TS, nullable=True),
    CheckConstraint("status IN ('active', 'deleted')", name="status_known"),
    CheckConstraint("(status = 'deleted') = (deleted_at IS NOT NULL)", name="deleted_at_matches"),
    CheckConstraint("char_length(nickname::text) BETWEEN 3 AND 48", name="nickname_length"),
    CheckConstraint("version >= 1 AND roles_version >= 0", name="versions_valid"),
)

password_credentials = Table(
    "password_credentials",
    _md,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("password_hash", Text, nullable=False),
    Column("updated_at", _TS, nullable=False),
)

auth_identities = Table(
    "auth_identities",
    _md,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("provider", String(32), nullable=False),
    Column("subject", String(255), nullable=False),
    Column("display", String(255), nullable=True),
    Column("linked_at", _TS, nullable=False),
    PrimaryKeyConstraint("provider", "subject"),  # аккаунт провайдера — максимум у одного
    UniqueConstraint("user_id", "provider"),  # этап 1: одна привязка на провайдера
)

sessions = Table(
    "sessions",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("created_at", _TS, nullable=False),
    Column("last_seen_at", _TS, nullable=True),
    Column("revoked_at", _TS, nullable=True),
    Column("revoke_reason", String(32), nullable=True),
    Column("ip_prefix", String(64), nullable=True),
    CheckConstraint("kind IN ('web', 'admin')", name="kind_known"),
    Index("ix_sessions_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
)

refresh_tokens = Table(
    "refresh_tokens",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("session_id", Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("parent_id", Uuid, nullable=True),
    Column("created_at", _TS, nullable=False),
    Column("expires_at", _TS, nullable=False),
    Column("used_at", _TS, nullable=True),
    Index("ix_refresh_tokens_session_id", "session_id"),
)

roles = Table(
    "roles",
    _md,
    Column("name", String(32), primary_key=True),
    Column("created_at", _TS, nullable=False, server_default=text("now()")),
)

permissions = Table(
    "permissions",
    _md,
    Column("name", String(64), primary_key=True),
    Column("description", Text, nullable=False, server_default=text("''")),
)

role_permissions = Table(
    "role_permissions",
    _md,
    Column("role", String(32), ForeignKey("roles.name", ondelete="CASCADE"), nullable=False),
    Column("perm", String(64), ForeignKey("permissions.name"), nullable=False),
    PrimaryKeyConstraint("role", "perm"),
)

user_roles = Table(
    "user_roles",
    _md,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(32), ForeignKey("roles.name"), nullable=False),
    PrimaryKeyConstraint("user_id", "role"),
    Index("ix_user_roles_role", "role"),
)

role_changes = Table(
    "role_changes",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("user_id", Uuid, nullable=True),  # без FK: аудит переживает любые чистки
    Column("actor_id", Uuid, nullable=True),
    Column("before", JSONB, nullable=False),
    Column("after", JSONB, nullable=False),
    Column("reason", Text, nullable=False),
    Column("created_at", _TS, nullable=False),
    CheckConstraint("kind IN ('user_roles', 'matrix')", name="kind_known"),
    Index("ix_role_changes_user_id", "user_id"),
)

consents = Table(
    "consents",
    _md,
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("version", String(32), nullable=False),
    Column("granted_at", _TS, nullable=False),
    PrimaryKeyConstraint("user_id", "kind", "version"),
)

email_tokens = Table(
    "email_tokens",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("purpose", String(16), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", _TS, nullable=False),
    Column("expires_at", _TS, nullable=False),
    Column("used_at", _TS, nullable=True),
    CheckConstraint("purpose IN ('verify', 'reset')", name="purpose_known"),
    Index("ix_email_tokens_user_id", "user_id"),
)

# Ключи идемпотентности admin-мутаций (gv_db.idempotency; миграция 0004).
idempotency_keys = idempotency_table(_md)
