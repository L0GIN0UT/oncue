"""Схема БД notifications (ADR 0047). Идентификаторы - UUID v7, время - timestamptz.

* ``notifications`` - уведомления игроков; ``UNIQUE(user_id, dedup_key)`` - ключ предмета
  события, а не ``event_id``;
* ``notification_preferences`` - отключение каналов по типам (нет строки - значения из конфига);
* ``email_subscriptions`` - согласие на информационную рассылку (по умолчанию нет);
* ``devices`` - устройства для Push (токен хранится хешем, в лог не попадает);
* ``broadcasts`` / ``broadcast_deliveries`` - рассылки администрации и факты отправки;
* ``finished_event_runs`` - завершённые запуски событий progression (``EventRunFinished``): по ним
  воркер массовой доставки прекращает рассылку (ADR 0054);
* ``notification_type_flags`` - решение админа о включённости типа (нет строки - включён, ADR 0066);
* ``admin_actions`` / ``idempotency_keys`` - аудит и ключи идемпотентности admin-мутаций.
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
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import create_metadata
from gv_db.idempotency import idempotency_table

TABLES = create_metadata()
_md = TABLES.metadata

notifications = Table(
    "notifications",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("type", String(64), nullable=False),
    Column("dedup_key", String(200), nullable=False),
    Column("title", String(120), nullable=False),
    Column("body", String(500), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("read_at", DateTime(timezone=True), nullable=True),
    Column("data", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_dedup"),
    Index("ix_notifications_user_created", "user_id", text("created_at DESC"), text("id DESC")),
    Index(
        "ix_notifications_user_unread",
        "user_id",
        postgresql_where=text("read_at IS NULL"),
    ),
)

notification_preferences = Table(
    "notification_preferences",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("type", String(64), nullable=False),
    Column("in_app", Boolean, nullable=False),
    Column("email", Boolean, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("user_id", "type", name="pk_notification_preferences"),
)

email_subscriptions = Table(
    "email_subscriptions",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("subscribed", Boolean, nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
    Index("ix_email_subscriptions_subscribed", "user_id", postgresql_where=text("subscribed")),
)

devices = Table(
    "devices",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("platform", String(16), nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("registered_at", DateTime(timezone=True), nullable=False),
    # Мягкое удаление (миграция 0003): отличает повторное удаление своего устройства от чужого.
    Column("deleted_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("platform", "token_hash", name="uq_devices_platform_token"),
    CheckConstraint("platform IN ('web', 'ios', 'android')", name="platform_known"),
    CheckConstraint("length(token_hash) = 64", name="token_hash_sha256"),
    Index("ix_devices_user", "user_id"),
)

broadcasts = Table(
    "broadcasts",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("subject", String(150), nullable=False),
    Column("body", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("recipients", Integer, nullable=False),
    Column("delivered", Integer, nullable=False, server_default=text("0")),
    Column("failed", Integer, nullable=False, server_default=text("0")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    CheckConstraint("status IN ('queued', 'sending', 'sent', 'failed')", name="status_known"),
    CheckConstraint(
        "recipients >= 0 AND delivered >= 0 AND failed >= 0", name="counts_non_negative"
    ),
    Index("ix_broadcasts_created", text("created_at DESC"), text("id DESC")),
)

finished_event_runs = Table(
    "finished_event_runs",
    _md,
    Column("run_id", Uuid(as_uuid=True), primary_key=True),
    Column("reason", String(16), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("reason IN ('ended', 'stopped')", name="reason_known"),
)

broadcast_deliveries = Table(
    "broadcast_deliveries",
    _md,
    Column("broadcast_id", Uuid(as_uuid=True), ForeignKey("broadcasts.id"), nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=False),
    # sent - письмо отправлено (или занято к отправке); failed - адрес отвергнут окончательно.
    Column("status", String(8), nullable=False, server_default=text("'sent'")),
    # Токен отписки получателя: только sha256 (hex); NULL - доставки до миграции 0004.
    Column("token_hash", String(64), nullable=True),
    Column("token_expires_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("status IN ('sent', 'failed')", name="status_known"),
    # UNIQUE(broadcast_id, user_id): повтор воркера не шлёт письмо дважды.
    PrimaryKeyConstraint("broadcast_id", "user_id", name="pk_broadcast_deliveries"),
    Index(
        "uq_broadcast_deliveries_token_hash",
        "token_hash",
        unique=True,
        postgresql_where=text("token_hash IS NOT NULL"),
    ),
    Index("ix_broadcast_deliveries_token_expires_at", "token_expires_at"),
)

notification_type_flags = Table(
    "notification_type_flags",
    _md,
    Column("type", String(64), primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("changed_by", Uuid(as_uuid=True), nullable=False),
    Column("changed_at", DateTime(timezone=True), nullable=False),
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
    Column("at", DateTime(timezone=True), nullable=False),
    Index("ix_admin_actions_target_at", "target", "at"),
    Index("ix_admin_actions_actor_at", "actor_id", "at"),
)

idempotency_keys = idempotency_table(_md)

__all__ = [
    "TABLES",
    "admin_actions",
    "broadcast_deliveries",
    "broadcasts",
    "devices",
    "email_subscriptions",
    "idempotency_keys",
    "notification_preferences",
    "notification_type_flags",
    "notifications",
]
