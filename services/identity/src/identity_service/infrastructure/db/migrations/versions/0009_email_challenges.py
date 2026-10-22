"""Одноразовые коды из писем и доверенные устройства (ADR 0041): expand.

Новые таблицы ``email_challenges`` и ``trusted_devices``. Частичный UNIQUE по
``(subject_hash, purpose)`` для действующих кодов закрепляет «один активный код» в схеме.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = "consumed_at IS NULL AND cancelled_at IS NULL"


def upgrade() -> None:
    op.create_table(
        "email_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("subject_hash", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("send_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("spent_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("device_hash", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('register', 'login', 'reset', 'password_change')",
            name=op.f("ck_email_challenges_purpose_known"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_email_challenges_attempts_non_negative")),
        sa.CheckConstraint(
            "max_attempts > 0", name=op.f("ck_email_challenges_max_attempts_positive")
        ),
        sa.CheckConstraint("send_count >= 1", name=op.f("ck_email_challenges_send_count_positive")),
        sa.CheckConstraint(
            "spent_total >= 0", name=op.f("ck_email_challenges_spent_total_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_challenges")),
    )
    op.create_index(
        "uq_email_challenges_active",
        "email_challenges",
        ["subject_hash", "purpose"],
        unique=True,
        postgresql_where=sa.text(ACTIVE),
    )
    op.create_index(
        "ix_email_challenges_subject_created",
        "email_challenges",
        ["subject_hash", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_challenges_expires_at", "email_challenges", ["expires_at"], unique=False
    )
    op.create_table(
        "trusted_devices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("device_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_trusted_devices_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trusted_devices")),
        sa.UniqueConstraint(
            "user_id", "device_hash", name=op.f("uq_trusted_devices_user_id_device_hash")
        ),
    )


def downgrade() -> None:
    op.drop_table("trusted_devices")
    op.drop_index("ix_email_challenges_expires_at", table_name="email_challenges")
    op.drop_index("ix_email_challenges_subject_created", table_name="email_challenges")
    op.drop_index(
        "uq_email_challenges_active",
        table_name="email_challenges",
        postgresql_where=sa.text(ACTIVE),
    )
    op.drop_table("email_challenges")
