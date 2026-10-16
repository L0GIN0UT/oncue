"""Ядро сборников: агрегат, версии, черновики, реакции, статистика, проекции (этап 3b, K1).

Только новые таблицы (expand), существующие ``outbox``/``inbox`` не меняются. GIN-индекс
``gin_trgm_ops`` требует расширения ``pg_trgm`` — его создаёт init-скрипт БД (у роли
сервиса нет прав суперпользователя). Таблицы пустые, поэтому индексы создаются обычным
``CREATE INDEX`` (без ``CONCURRENTLY``). Откат удаляет только эти таблицы.

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
        "admin_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
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
        "ix_admin_actions_target", "admin_actions", ["target", "created_at"], unique=False
    )
    op.create_table(
        "author_restrictions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_author_restrictions")),
    )
    op.create_table(
        "catalog_items_proj",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_catalog_items_proj_status_known")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_catalog_items_proj_version_positive")),
        sa.PrimaryKeyConstraint("media_item_id", name=op.f("pk_catalog_items_proj")),
    )
    op.create_index(
        "ix_catalog_items_proj_blocked",
        "catalog_items_proj",
        ["media_item_id"],
        unique=False,
        postgresql_where=sa.text("status = 'blocked'"),
    )
    op.create_table(
        "collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "visibility", sa.String(length=16), server_default=sa.text("'public'"), nullable=False
        ),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("content_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("draft_seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=32)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("cover_preset", sa.String(length=32), nullable=True),
        sa.Column("default_mode_key", sa.String(length=100), nullable=True),
        sa.Column("items_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("title_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("likes_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("plays_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ratio_sum", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("ratio_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("difficulty_score", sa.Float(), nullable=True),
        sa.Column("popular_score", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("featured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("featured_rank", sa.Integer(), nullable=True),
        sa.Column("hidden_reason", sa.String(length=500), nullable=True),
        sa.Column("hidden_by", sa.Uuid(), nullable=True),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "NOT featured OR status = 'published'", name=op.f("ck_collections_featured_published")
        ),
        sa.CheckConstraint("domain IN ('music', 'film')", name=op.f("ck_collections_domain_known")),
        sa.CheckConstraint(
            "kind = 'curated' OR status <> 'draft' OR author_id IS NOT NULL",
            name=op.f("ck_collections_draft_has_author"),
        ),
        sa.CheckConstraint(
            "kind = 'ugc' OR (author_id IS NULL AND status <> 'draft')",
            name=op.f("ck_collections_curated_consistent"),
        ),
        sa.CheckConstraint("kind IN ('ugc', 'curated')", name=op.f("ck_collections_kind_known")),
        sa.CheckConstraint(
            "status <> 'deleted' OR deleted_at IS NOT NULL",
            name=op.f("ck_collections_deleted_has_time"),
        ),
        sa.CheckConstraint(
            "status <> 'draft' OR content_version = 0",
            name=op.f("ck_collections_draft_unpublished"),
        ),
        sa.CheckConstraint(
            "status <> 'hidden' OR hidden_at IS NOT NULL",
            name=op.f("ck_collections_hidden_has_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'hidden', 'deleted')",
            name=op.f("ck_collections_status_known"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('published', 'hidden') OR (content_version >= 1 "
            "AND first_published_at IS NOT NULL AND published_at IS NOT NULL "
            "AND default_mode_key IS NOT NULL)",
            name=op.f("ck_collections_published_complete"),
        ),
        sa.CheckConstraint("title <> ''", name=op.f("ck_collections_title_not_empty")),
        sa.CheckConstraint(
            "visibility IN ('public', 'unlisted')", name=op.f("ck_collections_visibility_known")
        ),
        sa.CheckConstraint(
            "difficulty_score IS NULL OR difficulty_score BETWEEN 0 AND 1",
            name=op.f("ck_collections_difficulty_range"),
        ),
        sa.CheckConstraint(
            "items_count BETWEEN 0 AND 200", name=op.f("ck_collections_items_count_range")
        ),
        sa.CheckConstraint(
            "likes_count >= 0 AND plays_count >= 0 AND ratio_count >= 0 AND ratio_sum >= 0",
            name=op.f("ck_collections_counters_non_negative"),
        ),
        sa.CheckConstraint(
            "version >= 0 AND content_version >= 0 AND draft_seq >= 0",
            name=op.f("ck_collections_versions_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collections")),
    )
    op.create_index(
        "ix_collections_author_updated",
        "collections",
        ["author_id", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_collections_catalog_difficulty",
        "collections",
        [
            # То же выражение, что ORDER BY каталога: без сложности — в конце.
            sa.literal_column("(COALESCE(difficulty_score, '-1'::double precision)) DESC"),
            sa.literal_column("id DESC"),
        ],
        unique=False,
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.create_index(
        "ix_collections_catalog_featured",
        "collections",
        ["featured_rank", "id"],
        unique=False,
        postgresql_where=sa.text("featured AND status = 'published' AND visibility = 'public'"),
    )
    op.create_index(
        "ix_collections_catalog_likes",
        "collections",
        ["likes_count", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.create_index(
        "ix_collections_catalog_new",
        "collections",
        ["first_published_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.create_index(
        "ix_collections_catalog_popular",
        "collections",
        ["popular_score", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.create_index(
        "ix_collections_title_trgm",
        "collections",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.create_index(
        "ix_collections_title_tsv",
        "collections",
        ["title_tsv"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index("ix_collections_updated", "collections", ["updated_at", "id"], unique=False)
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
        "users_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_users_proj_version_positive")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users_proj")),
    )
    op.create_table(
        "collection_drafts",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("items_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("client_label", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_collection_drafts_revision_positive")),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_collection_drafts_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("collection_id", name=op.f("pk_collection_drafts")),
    )
    op.create_table(
        "collection_items",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("emoji", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "position BETWEEN 0 AND 199", name=op.f("ck_collection_items_position_range")
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_collection_items_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("collection_id", "position", name=op.f("pk_collection_items")),
    )
    op.create_index(
        "ix_collection_items_media_item",
        "collection_items",
        ["media_item_id", "collection_id"],
        unique=False,
    )
    op.create_table(
        "collection_versions",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_version >= 1", name=op.f("ck_collection_versions_content_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_collection_versions_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "collection_id", "content_version", name=op.f("pk_collection_versions")
        ),
    )
    op.create_table(
        "completions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("ratio", sa.Float(), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "content_version >= 1", name=op.f("ck_completions_content_version_positive")
        ),
        sa.CheckConstraint(
            "ratio IS NULL OR ratio BETWEEN 0 AND 1", name=op.f("ck_completions_ratio_range")
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_completions_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "collection_id", "content_version", name=op.f("pk_completions")
        ),
    )
    op.create_index("ix_completions_collection", "completions", ["collection_id"], unique=False)
    op.create_table(
        "favorites",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_favorites_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "collection_id", name=op.f("pk_favorites")),
    )
    op.create_index("ix_favorites_collection", "favorites", ["collection_id"], unique=False)
    op.create_index(
        "ix_favorites_user_created",
        "favorites",
        ["user_id", "created_at", "collection_id"],
        unique=False,
    )
    op.create_table(
        "likes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_likes_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "collection_id", name=op.f("pk_likes")),
    )
    op.create_index("ix_likes_collection", "likes", ["collection_id"], unique=False)
    op.create_index(
        "ix_likes_user_created", "likes", ["user_id", "created_at", "collection_id"], unique=False
    )
    op.create_table(
        "play_stats",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("likes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("plays", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "likes >= 0 AND plays >= 0", name=op.f("ck_play_stats_counters_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collections.id"],
            name=op.f("fk_play_stats_collection_id_collections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("collection_id", "day", name=op.f("pk_play_stats")),
    )
    op.create_index("ix_play_stats_day", "play_stats", ["day"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_play_stats_day", table_name="play_stats")
    op.drop_table("play_stats")
    op.drop_index("ix_likes_user_created", table_name="likes")
    op.drop_index("ix_likes_collection", table_name="likes")
    op.drop_table("likes")
    op.drop_index("ix_favorites_user_created", table_name="favorites")
    op.drop_index("ix_favorites_collection", table_name="favorites")
    op.drop_table("favorites")
    op.drop_index("ix_completions_collection", table_name="completions")
    op.drop_table("completions")
    op.drop_table("collection_versions")
    op.drop_index("ix_collection_items_media_item", table_name="collection_items")
    op.drop_table("collection_items")
    op.drop_table("collection_drafts")
    op.drop_table("users_proj")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_collections_updated", table_name="collections")
    op.drop_index("ix_collections_title_tsv", table_name="collections", postgresql_using="gin")
    op.drop_index(
        "ix_collections_title_trgm",
        table_name="collections",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_collections_catalog_popular",
        table_name="collections",
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.drop_index(
        "ix_collections_catalog_new",
        table_name="collections",
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.drop_index(
        "ix_collections_catalog_likes",
        table_name="collections",
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.drop_index(
        "ix_collections_catalog_featured",
        table_name="collections",
        postgresql_where=sa.text("featured AND status = 'published' AND visibility = 'public'"),
    )
    op.drop_index(
        "ix_collections_catalog_difficulty",
        table_name="collections",
        postgresql_where=sa.text("status = 'published' AND visibility = 'public'"),
    )
    op.drop_index("ix_collections_author_updated", table_name="collections")
    op.drop_table("collections")
    op.drop_index(
        "ix_catalog_items_proj_blocked",
        table_name="catalog_items_proj",
        postgresql_where=sa.text("status = 'blocked'"),
    )
    op.drop_table("catalog_items_proj")
    op.drop_table("author_restrictions")
    op.drop_index("ix_admin_actions_target", table_name="admin_actions")
    op.drop_table("admin_actions")
