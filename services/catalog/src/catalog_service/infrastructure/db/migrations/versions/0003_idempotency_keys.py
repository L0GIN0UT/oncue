"""Ключи идемпотентности admin-мутаций (``gv_db.idempotency``).

Expand: новая таблица ``idempotency_keys`` и перенос в неё уже выданных ответов из
``admin_actions`` — отпечаток запроса считается тем же алгоритмом
(``gv_db.idempotency.request_fingerprint``), поэтому повтор с ключом, выданным до
миграции, по-прежнему вернёт сохранённый ответ. ``admin_actions`` остаётся аудитом;
её колонки ``request_hash``/``response`` не трогаем (удаление — отдельным релизом, если
понадобится).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
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
    # Таблица только что создана и пуста — перенос не конфликтует и не держит блокировок
    # на чужих данных; admin_actions на этапе 1 маленькая (ручные действия модераторов).
    op.execute(
        """
        INSERT INTO idempotency_keys
            (actor_id, key, request_hash, response, status_code, created_at)
        SELECT actor_id, idempotency_key, request_hash, response, 200, created_at
        FROM admin_actions
        WHERE length(request_hash) = 64
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
