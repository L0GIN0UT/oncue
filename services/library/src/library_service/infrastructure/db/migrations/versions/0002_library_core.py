"""Ядро library: полки, единицы полок, проекция catalog, версии, удаление пользователей.

Только новые таблицы (expand-only, план этапа 3b §13): таблицы пустые, блокировок на
существующих данных нет.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_items_proj",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column(
            "persons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "domain IN ('music', 'film')", name=op.f("ck_catalog_items_proj_domain_valid")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_catalog_items_proj_status_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_catalog_items_proj_version_positive")),
        sa.PrimaryKeyConstraint("media_item_id", name=op.f("pk_catalog_items_proj")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299",
            name=op.f("ck_idempotency_keys_status_code_success"),
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )
    op.create_table(
        "shelves",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("items_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'system' AND key IS NOT NULL AND title IS NULL)"
            " OR (kind = 'custom' AND key IS NULL AND title IS NOT NULL)",
            name=op.f("ck_shelves_kind_fields"),
        ),
        sa.CheckConstraint("items_count >= 0", name=op.f("ck_shelves_items_count_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shelves")),
        sa.UniqueConstraint("user_id", "key", name=op.f("uq_shelves_user_id_key")),
    )
    op.create_table(
        "user_purges",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_purges")),
    )
    op.create_index("ix_user_purges_requested_at", "user_purges", ["requested_at"], unique=False)
    op.create_table(
        "user_versions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 0", name=op.f("ck_user_versions_version_non_negative")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_versions")),
    )
    op.create_table(
        "shelf_items",
        sa.Column("shelf_id", sa.Uuid(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("domain IN ('music', 'film')", name=op.f("ck_shelf_items_domain_valid")),
        sa.CheckConstraint(
            "source IN ('reveal', 'results', 'card', 'collection', 'library')",
            name=op.f("ck_shelf_items_source_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["shelf_id"],
            ["shelves.id"],
            name=op.f("fk_shelf_items_shelf_id_shelves"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("shelf_id", "media_item_id", name=op.f("pk_shelf_items")),
    )
    op.create_index(
        "ix_shelf_items_shelf_id_added_at",
        "shelf_items",
        ["shelf_id", "added_at", "media_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_shelf_items_user_id_media_item_id",
        "shelf_items",
        ["user_id", "media_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shelf_items_user_id_media_item_id", table_name="shelf_items")
    op.drop_index("ix_shelf_items_shelf_id_added_at", table_name="shelf_items")
    op.drop_table("shelf_items")
    op.drop_table("user_versions")
    op.drop_index("ix_user_purges_requested_at", table_name="user_purges")
    op.drop_table("user_purges")
    op.drop_table("shelves")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_table("catalog_items_proj")
