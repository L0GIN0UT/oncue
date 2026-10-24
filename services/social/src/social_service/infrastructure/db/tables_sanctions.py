"""Таблицы санкций social (5c, ADR 0052). Время - timestamptz.

* ``sanctions`` - история (append-only, кроме полей снятия); одна не снятая запись на
  ``(user_id, kind)`` (частичный UNIQUE): новая выдача закрывает прежнюю как ``replaced``;
* ``sanction_state`` - монотонная ``version`` набора санкций игрока; строка блокируется
  ``INSERT .. ON CONFLICT DO UPDATE`` в транзакции изменения, что сериализует гонки выдач.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    Uuid,
    text,
)

from social_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata
_TS = DateTime(timezone=True)

sanctions = Table(
    "sanctions",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("started_at", _TS, nullable=False),
    Column("expires_at", _TS, nullable=True),
    Column("reason", String(500), nullable=False),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("revoked_at", _TS, nullable=True),
    Column("revoked_by", Uuid(as_uuid=True), nullable=True),
    Column("revoke_reason", String(500), nullable=True),
    CheckConstraint("kind IN ('ranked', 'chat', 'online', 'account')", name="kind_known"),
    CheckConstraint("expires_at IS NULL OR expires_at > started_at", name="expires_after_start"),
    CheckConstraint("(revoked_at IS NULL) = (revoke_reason IS NULL)", name="revoked_pair"),
    CheckConstraint("revoked_by IS NULL OR revoked_at IS NOT NULL", name="revoked_by_needs_revoke"),
    Index(
        "uq_sanctions_open_kind",
        "user_id",
        "kind",
        unique=True,
        postgresql_where=text("revoked_at IS NULL"),
    ),
    Index("ix_sanctions_user_started", "user_id", text("started_at DESC")),
    Index(
        "ix_sanctions_open_expires",
        "expires_at",
        postgresql_where=text("revoked_at IS NULL AND expires_at IS NOT NULL"),
    ),
)

sanction_state = Table(
    "sanction_state",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("version", Integer, nullable=False),
    CheckConstraint("version >= 1", name="version_positive"),
)
