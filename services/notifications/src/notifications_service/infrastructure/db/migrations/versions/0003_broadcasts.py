"""Рассылки администрации: broadcasts, факты отправки, аудит, ключи идемпотентности.

Только новые таблицы (expand). ``broadcast_deliveries`` - PK ``(broadcast_id, user_id)``: повтор
воркера не отправляет письмо дважды. Откат удаляет эти таблицы целиком.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Мягкое удаление устройств (nullable-колонка без значения по умолчанию - без перезаписи).
    op.add_column("devices", sa.Column("deleted_at", _TS, nullable=True))
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=150), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("delivered", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'sending', 'sent', 'failed')",
            name=op.f("ck_broadcasts_status_known"),
        ),
        sa.CheckConstraint(
            "recipients >= 0 AND delivered >= 0 AND failed >= 0",
            name=op.f("ck_broadcasts_counts_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_broadcasts")),
    )
    op.create_index(
        "ix_broadcasts_created",
        "broadcasts",
        [sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    op.create_table(
        "broadcast_deliveries",
        sa.Column("broadcast_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sent_at", _TS, nullable=False),
        sa.Column("status", sa.String(length=8), server_default=sa.text("'sent'"), nullable=False),
        sa.CheckConstraint(
            "status IN ('sent', 'failed')", name=op.f("ck_broadcast_deliveries_status_known")
        ),
        sa.ForeignKeyConstraint(
            ["broadcast_id"],
            ["broadcasts.id"],
            name=op.f("fk_broadcast_deliveries_broadcast_id_broadcasts"),
        ),
        sa.PrimaryKeyConstraint("broadcast_id", "user_id", name="pk_broadcast_deliveries"),
    )
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
    )
    op.create_index("ix_admin_actions_target_at", "admin_actions", ["target", "at"], unique=False)
    op.create_index("ix_admin_actions_actor_at", "admin_actions", ["actor_id", "at"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", _JSONB, nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299", name=op.f("ck_idempotency_keys_status_code_success")
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_admin_actions_actor_at", table_name="admin_actions")
    op.drop_index("ix_admin_actions_target_at", table_name="admin_actions")
    op.drop_table("admin_actions")
    op.drop_table("broadcast_deliveries")
    op.drop_index("ix_broadcasts_created", table_name="broadcasts")
    op.drop_table("broadcasts")
    op.drop_column("devices", "deleted_at")
