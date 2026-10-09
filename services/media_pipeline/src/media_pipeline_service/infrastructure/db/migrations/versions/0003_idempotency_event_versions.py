"""Идемпотентность через gv_db, версия событий по единице, хэш субъекта в журнале ссылок.

* ``idempotency_keys`` пересоздаётся по схеме ``gv_db.idempotency.idempotency_table``
  (ключ ``(actor_id, key)``, ответ обязателен — строка пишется в транзакции мутации).
  Старая таблица (резерв ключа отдельной транзакцией) удаляется: данных в проде нет, а ключи
  идемпотентности — короткоживущие (потеря ключа = повтор выполнится заново).
* ``media_items_state.events_version`` — счётчик версий событий AssetsReady/AssetsFailed по
  единице; добавление NOT NULL c DEFAULT 0 в PostgreSQL 11+ не переписывает таблицу.
* ``link_issuance.subject`` → ``subject_hash`` (sha256 hex): ПДн в журнале не храним.
  Существующие строки хэшируются на месте.

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
    # --- идемпотентность: старая схема (scope, key, ответ nullable) → gv_db.idempotency
    op.drop_table("idempotency_keys")
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299", name=op.f("ck_idempotency_keys_status_code_success")
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )

    # --- версия событий по единице
    op.add_column(
        "media_items_state",
        sa.Column("events_version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_media_items_state_events_version_non_negative"),
        "media_items_state",
        "events_version >= 0",
    )

    # --- журнал ссылок: только хэш субъекта
    op.alter_column("link_issuance", "subject", new_column_name="subject_hash")
    op.execute(
        "UPDATE link_issuance "
        "SET subject_hash = encode(sha256(convert_to(subject_hash, 'UTF8')), 'hex')"
    )
    op.alter_column(
        "link_issuance",
        "subject_hash",
        type_=sa.String(length=64),
        existing_type=sa.String(length=100),
        existing_nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_link_issuance_subject_hash_sha256"),
        "link_issuance",
        "subject_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    # Хэш не обратим: при откате в колонке остаются хэши (для журнала это допустимо).
    op.drop_constraint(op.f("ck_link_issuance_subject_hash_sha256"), "link_issuance", type_="check")
    op.alter_column(
        "link_issuance",
        "subject_hash",
        type_=sa.String(length=100),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column("link_issuance", "subject_hash", new_column_name="subject")

    op.drop_constraint(
        op.f("ck_media_items_state_events_version_non_negative"),
        "media_items_state",
        type_="check",
    )
    op.drop_column("media_items_state", "events_version")

    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.String(length=200), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope", "key", name=op.f("pk_idempotency_keys")),
    )
