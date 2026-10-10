"""Ядро game: конфиги режимов, проекции, наборы дня, сессии, статистика, аудит (expand-only).

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
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
    )
    op.create_index(
        "ix_admin_actions_target_created_at",
        "admin_actions",
        ["target", "created_at"],
        unique=False,
    )
    op.create_table(
        "catalog_items",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "persons",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "genres", sa.ARRAY(sa.Text()), server_default=sa.text("'{}'::text[]"), nullable=False
        ),
        sa.Column("popularity", sa.Float(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_catalog_items_status_valid")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_catalog_items_version_positive")),
        sa.PrimaryKeyConstraint("media_item_id", name=op.f("pk_catalog_items")),
    )
    op.create_index(
        "ix_catalog_items_domain_kind", "catalog_items", ["domain", "kind"], unique=False
    )
    op.create_index(
        "ix_catalog_items_genres", "catalog_items", ["genres"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "clue_assets",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("clue_type", sa.String(length=64), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("asset_set_id", sa.Uuid(), nullable=False),
        sa.Column("levels_count", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ready', 'failed')", name=op.f("ck_clue_assets_status_valid")
        ),
        sa.CheckConstraint("levels_count >= 1", name=op.f("ck_clue_assets_levels_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_clue_assets_version_positive")),
        sa.PrimaryKeyConstraint(
            "media_item_id", "clue_type", "params_hash", name=op.f("pk_clue_assets")
        ),
    )
    op.create_index("ix_clue_assets_asset_set_id", "clue_assets", ["asset_set_id"], unique=False)
    op.create_table(
        "daily_round_stats",
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("played", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("exact", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("partial", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "played >= 0 AND exact >= 0 AND partial >= 0 AND exact + partial <= played",
            name=op.f("ck_daily_round_stats_counts_valid"),
        ),
        sa.PrimaryKeyConstraint("mode_key", "day", "idx", name=op.f("pk_daily_round_stats")),
    )
    op.create_table(
        "daily_sets",
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("mode_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("generated_by", sa.String(length=16), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status <> 'locked' OR locked_at IS NOT NULL",
            name=op.f("ck_daily_sets_locked_has_time"),
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'locked', 'failed')", name=op.f("ck_daily_sets_status_valid")
        ),
        sa.CheckConstraint("generation >= 1", name=op.f("ck_daily_sets_generation_positive")),
        sa.PrimaryKeyConstraint("mode_key", "day", name=op.f("pk_daily_sets")),
    )
    op.create_table(
        "game_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("player_kind", sa.String(length=16), nullable=False),
        sa.Column("player_id", sa.Uuid(), nullable=True),
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("mode_version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("clue_type", sa.String(length=64), nullable=False),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("preset_id", sa.String(length=64), nullable=True),
        sa.Column("seed", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idle_timeout_s", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_move_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "player_kind IN ('user', 'guest')", name=op.f("ck_game_sessions_player_kind_valid")
        ),
        sa.CheckConstraint(
            "status = 'active' OR finished_at IS NOT NULL",
            name=op.f("ck_game_sessions_finished_has_time"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'finished', 'abandoned')",
            name=op.f("ck_game_sessions_status_valid"),
        ),
        sa.CheckConstraint(
            "idle_timeout_s > 0", name=op.f("ck_game_sessions_idle_timeout_positive")
        ),
        sa.CheckConstraint(
            "score >= 0 AND max_score >= 0", name=op.f("ck_game_sessions_score_non_negative")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_game_sessions_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_game_sessions")),
    )
    op.create_index(
        "ix_game_sessions_active_last_move",
        "game_sessions",
        ["last_move_at"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_game_sessions_player",
        "game_sessions",
        ["player_kind", "player_id", sa.literal_column("started_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_game_sessions_daily_attempt",
        "game_sessions",
        ["mode_key", "day", "player_kind", "player_id"],
        unique=True,
        postgresql_where=sa.text("day IS NOT NULL"),
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
            "status_code BETWEEN 200 AND 299", name=op.f("ck_idempotency_keys_status_code_success")
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )
    op.create_table(
        "mode_config_drafts",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_mode_config_drafts_revision_positive")),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_mode_config_drafts")),
    )
    op.create_table(
        "mode_configs",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("rolled_back_from", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "domain IN ('music', 'film')", name=op.f("ck_mode_configs_domain_valid")
        ),
        sa.CheckConstraint(
            "status IN ('published', 'archived')", name=op.f("ck_mode_configs_status_valid")
        ),
        sa.CheckConstraint("length(body_hash) = 64", name=op.f("ck_mode_configs_body_hash_sha256")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_mode_configs_version_positive")),
        sa.PrimaryKeyConstraint("key", "version", name=op.f("pk_mode_configs")),
    )
    op.create_table(
        "daily_set_items",
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("idx", sa.SmallInteger(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("idx >= 0", name=op.f("ck_daily_set_items_idx_non_negative")),
        sa.ForeignKeyConstraint(
            ["mode_key", "day"],
            ["daily_sets.mode_key", "daily_sets.day"],
            name=op.f("fk_daily_set_items_mode_key_day_daily_sets"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("mode_key", "day", "idx", name=op.f("pk_daily_set_items")),
        sa.UniqueConstraint(
            "mode_key",
            "day",
            "media_item_id",
            name=op.f("uq_daily_set_items_mode_key_day_media_item_id"),
        ),
    )
    op.create_index(
        "ix_daily_set_items_mode_key_media_item_id_day",
        "daily_set_items",
        ["mode_key", "media_item_id", "day"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_daily_set_items_mode_key_media_item_id_day", table_name="daily_set_items")
    op.drop_table("daily_set_items")
    op.drop_table("mode_configs")
    op.drop_table("mode_config_drafts")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index(
        "uq_game_sessions_daily_attempt",
        table_name="game_sessions",
        postgresql_where=sa.text("day IS NOT NULL"),
    )
    op.drop_index("ix_game_sessions_player", table_name="game_sessions")
    op.drop_index(
        "ix_game_sessions_active_last_move",
        table_name="game_sessions",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("game_sessions")
    op.drop_table("daily_sets")
    op.drop_table("daily_round_stats")
    op.drop_index("ix_clue_assets_asset_set_id", table_name="clue_assets")
    op.drop_table("clue_assets")
    op.drop_index("ix_catalog_items_genres", table_name="catalog_items", postgresql_using="gin")
    op.drop_index("ix_catalog_items_domain_kind", table_name="catalog_items")
    op.drop_table("catalog_items")
    op.drop_index("ix_admin_actions_target_created_at", table_name="admin_actions")
    op.drop_table("admin_actions")
