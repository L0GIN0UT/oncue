"""Ядро media-pipeline: проекция единиц, исходники, наборы и уровни ассетов, журнал ссылок,
ключи идемпотентности и аудит admin-действий (план этапа 1, §6).

Только создание новых таблиц — обратно совместимо.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
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
        "admin_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(reason) > 0", name=op.f("ck_admin_actions_reason_not_empty")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
    )
    op.create_index("ix_admin_actions_target_id", "admin_actions", ["target_id"], unique=False)
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
    op.create_table(
        "link_issuance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_set_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_link_issuance")),
    )
    op.create_index(
        "ix_link_issuance_asset_set_id", "link_issuance", ["asset_set_id"], unique=False
    )
    op.create_index("ix_link_issuance_issued_at", "link_issuance", ["issued_at"], unique=False)
    op.create_table(
        "media_items_state",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_media_items_state_status_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_media_items_state_version_positive")),
        sa.PrimaryKeyConstraint("media_item_id", name=op.f("pk_media_items_state")),
    )
    op.create_table(
        "source_media",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("uri", sa.String(length=2000), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.String(length=80), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('audio', 'frame', 'poster')", name=op.f("ck_source_media_kind_valid")
        ),
        sa.CheckConstraint(
            "origin IN ('upload', 'tmdb')", name=op.f("ck_source_media_origin_valid")
        ),
        sa.CheckConstraint(
            "status <> 'ready' OR (object_key IS NOT NULL AND sha256 IS NOT NULL AND bytes > 0)",
            name=op.f("ck_source_media_ready_has_object"),
        ),
        sa.CheckConstraint(
            "status IN ('registered', 'ready', 'failed')", name=op.f("ck_source_media_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items_state.media_item_id"],
            name=op.f("fk_source_media_media_item_id_media_items_state"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_media")),
        sa.UniqueConstraint("object_key", name=op.f("uq_source_media_object_key")),
    )
    op.create_index(
        "ix_source_media_media_item_id", "source_media", ["media_item_id"], unique=False
    )
    op.create_table(
        "asset_sets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("clue_type", sa.String(length=64), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("levels_count", sa.Integer(), nullable=False),
        sa.Column("source_media_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed', 'disabled')",
            name=op.f("ck_asset_sets_status_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_asset_sets_attempts_non_negative")),
        sa.CheckConstraint("generation >= 1", name=op.f("ck_asset_sets_generation_positive")),
        sa.CheckConstraint(
            "levels_count BETWEEN 1 AND 20", name=op.f("ck_asset_sets_levels_count_range")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_asset_sets_version_positive")),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items_state.media_item_id"],
            name=op.f("fk_asset_sets_media_item_id_media_items_state"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_media_id"],
            ["source_media.id"],
            name=op.f("fk_asset_sets_source_media_id_source_media"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_sets")),
        sa.UniqueConstraint(
            "media_item_id",
            "clue_type",
            "profile_key",
            "params_hash",
            name=op.f("uq_asset_sets_media_item_id_clue_type_profile_key_params_hash"),
        ),
    )
    op.create_table(
        "asset_levels",
        sa.Column("asset_set_id", sa.Uuid(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=80), nullable=False),
        sa.Column("mime", sa.String(length=64), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.CheckConstraint("bytes >= 0", name=op.f("ck_asset_levels_bytes_non_negative")),
        sa.CheckConstraint("level >= 1", name=op.f("ck_asset_levels_level_positive")),
        sa.ForeignKeyConstraint(
            ["asset_set_id"],
            ["asset_sets.id"],
            name=op.f("fk_asset_levels_asset_set_id_asset_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("asset_set_id", "level", name=op.f("pk_asset_levels")),
        sa.UniqueConstraint("object_key", name=op.f("uq_asset_levels_object_key")),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_table("asset_levels")
    op.drop_table("asset_sets")
    op.drop_index("ix_source_media_media_item_id", table_name="source_media")
    op.drop_table("source_media")
    op.drop_table("media_items_state")
    op.drop_index("ix_link_issuance_issued_at", table_name="link_issuance")
    op.drop_index("ix_link_issuance_asset_set_id", table_name="link_issuance")
    op.drop_table("link_issuance")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_admin_actions_target_id", table_name="admin_actions")
    op.drop_table("admin_actions")
