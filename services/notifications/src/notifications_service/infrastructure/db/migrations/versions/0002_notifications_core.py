"""Ядро notifications: уведомления, настройки по типам, согласие на рассылку, устройства.

Только новые таблицы (expand). ``UNIQUE(user_id, dedup_key)`` - дедупликация по предмету
события; токен устройства хранится хешем (sha256 hex). Откат удаляет эти таблицы целиком.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("read_at", _TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_dedup"),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("in_app", sa.Boolean(), nullable=False),
        sa.Column("email", sa.Boolean(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "type", name="pk_notification_preferences"),
    )
    op.create_table(
        "email_subscriptions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subscribed", sa.Boolean(), nullable=False),
        sa.Column("changed_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_email_subscriptions")),
    )
    op.create_index(
        "ix_email_subscriptions_subscribed",
        "email_subscriptions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("subscribed"),
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("registered_at", _TS, nullable=False),
        sa.CheckConstraint(
            "platform IN ('web', 'ios', 'android')", name=op.f("ck_devices_platform_known")
        ),
        sa.CheckConstraint("length(token_hash) = 64", name=op.f("ck_devices_token_hash_sha256")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("platform", "token_hash", name="uq_devices_platform_token"),
    )
    op.create_index("ix_devices_user", "devices", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_devices_user", table_name="devices")
    op.drop_table("devices")
    op.drop_index(
        "ix_email_subscriptions_subscribed",
        table_name="email_subscriptions",
        postgresql_where=sa.text("subscribed"),
    )
    op.drop_table("email_subscriptions")
    op.drop_table("notification_preferences")
    op.drop_index(
        "ix_notifications_user_unread",
        table_name="notifications",
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
