"""Ключи идемпотентности admin-мутаций (gv_db.idempotency).

Новая пустая таблица — без блокировок существующих данных.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299",
            name=op.f("ck_idempotency_keys_status_code_success"),
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
