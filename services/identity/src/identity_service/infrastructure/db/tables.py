"""Схема БД identity. Новые таблицы объявлять на ``TABLES.metadata``.

Инварианты закреплены в схеме: уникальность ника/e-mail без учёта регистра (``citext``),
аккаунт провайдера привязан максимум к одному пользователю, токены хранятся только хэшами.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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
    # NULL у гостевой сессии (миграция 0006, D5 плана этапа 2).
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    Column("kind", String(16), nullable=False),
    Column("created_at", _TS, nullable=False),
    Column("last_seen_at", _TS, nullable=True),
    Column("revoked_at", _TS, nullable=True),
    Column("revoke_reason", String(32), nullable=True),
    Column("ip_prefix", String(64), nullable=True),
    Column("subject_kind", String(16), nullable=False, server_default=text("'user'")),
    Column("guest_id", Uuid, nullable=True),
    CheckConstraint("kind IN ('web', 'admin')", name="kind_known"),
    CheckConstraint("subject_kind IN ('user', 'guest')", name="subject_kind_known"),
    CheckConstraint(
        "(subject_kind = 'user' AND user_id IS NOT NULL AND guest_id IS NULL) OR "
        "(subject_kind = 'guest' AND user_id IS NULL AND guest_id IS NOT NULL AND kind = 'web' "
        "AND last_seen_at IS NOT NULL)",
        name="subject_consistent",
    ),
    Index("ix_sessions_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
    Index(
        "ix_sessions_guest_id",
        "guest_id",
        unique=True,
        postgresql_where=text("guest_id IS NOT NULL"),
    ),
    # Очистка заброшенных гостевых сессий (PurgeGuestSessions).
    Index(
        "ix_sessions_guest_last_seen",
        "last_seen_at",
        postgresql_where=text("subject_kind = 'guest'"),
    ),
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

# Одноразовые коды из писем (ADR 0041, миграция 0009): в БД только HMAC кода.
email_challenges = Table(
    "email_challenges",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("purpose", String(32), nullable=False),
    Column("subject_hash", Text, nullable=False),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
    Column("code_hash", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False),
    Column("send_count", Integer, nullable=False, server_default=text("1")),
    # Всего списанных попыток за жизнь записи: resend обнуляет attempts, но не это (потолок).
    Column("spent_total", Integer, nullable=False, server_default=text("0")),
    # sha256 одноразового cookie gv_bind: подтвердить код можно только из этого браузера.
    Column("device_hash", Text, nullable=True),
    # Ключ лимита писем на адрес (хэш адреса): одинаков у кода и пустышки (миграция 0012).
    Column("mail_key", Text, nullable=True),
    Column("session_id", Uuid, nullable=True),
    Column("created_at", _TS, nullable=False),
    Column("expires_at", _TS, nullable=False),
    Column("last_sent_at", _TS, nullable=False),
    Column("consumed_at", _TS, nullable=True),
    Column("cancelled_at", _TS, nullable=True),
    CheckConstraint(
        "purpose IN ('register', 'login', 'reset', 'password_change')", name="purpose_known"
    ),
    CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
    CheckConstraint("send_count >= 1", name="send_count_positive"),
    CheckConstraint("spent_total >= 0", name="spent_total_non_negative"),
    # Инварианты, которые иначе держатся только кодом (миграция 0011).
    CheckConstraint("attempts <= max_attempts", name="attempts_within_max"),
    CheckConstraint("spent_total >= attempts", name="spent_covers_attempts"),
    CheckConstraint("expires_at > created_at", name="expires_after_created"),
    CheckConstraint(
        "NOT (consumed_at IS NOT NULL AND cancelled_at IS NOT NULL)", name="closed_once"
    ),
    CheckConstraint(
        "purpose NOT IN ('login', 'password_change') OR user_id IS NOT NULL",
        name="user_bound_purposes_have_user",
    ),
    # Один активный код на (субъект, назначение).
    Index(
        "uq_email_challenges_active",
        "subject_hash",
        "purpose",
        unique=True,
        postgresql_where=text("consumed_at IS NULL AND cancelled_at IS NULL"),
    ),
    # failures_since и очистка по сроку.
    Index("ix_email_challenges_subject_created", "subject_hash", "created_at"),
    Index("ix_email_challenges_expires_at", "expires_at"),
    # Каскад по ``users`` и удаление кодов аккаунта (DeleteAccount) без seq scan.
    Index(
        "ix_email_challenges_user_id",
        "user_id",
        postgresql_where=text("user_id IS NOT NULL"),
    ),
)

# Доверенные устройства (ADR 0041): sha256 cookie ``gv_device``.
trusted_devices = Table(
    "trusted_devices",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("device_hash", Text, nullable=False),
    Column("created_at", _TS, nullable=False),
    Column("expires_at", _TS, nullable=False),
    Column("revoked_at", _TS, nullable=True),
    UniqueConstraint("user_id", "device_hash"),
    # Очистка (PurgeEmailChallenges): истёкшие и давно отозванные устройства.
    Index("ix_trusted_devices_expires_at", "expires_at"),
    Index(
        "ix_trusted_devices_revoked_at",
        "revoked_at",
        postgresql_where=text("revoked_at IS NOT NULL"),
    ),
)

# Ключи идемпотентности admin-мутаций (gv_db.idempotency; миграция 0004).
idempotency_keys = idempotency_table(_md)

# Санкции игрока из событий social (ADR 0052; миграция 0017). ``active`` - записи вида и срока:
# по ним маска и бан считаются на момент выпуска токена (истёкшие не входят).
user_sanctions = Table(
    "user_sanctions",
    _md,
    Column("user_id", Uuid, primary_key=True),
    Column("mask", Integer, nullable=False),
    Column("account_banned", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    Column("next_expiry_at", _TS, nullable=True),
    Column("active", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("updated_at", _TS, nullable=False),
    CheckConstraint("version >= 1", name="sanctions_version_positive"),
    CheckConstraint("mask BETWEEN 0 AND 65535", name="sanctions_mask_range"),
)
