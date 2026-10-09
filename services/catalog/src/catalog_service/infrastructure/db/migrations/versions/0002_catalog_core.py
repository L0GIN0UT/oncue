"""Ядро каталога: единицы, алиасы, персоны, жанры, внешние id, метрики, исходники,
статусы ассетов загадок, задания импорта и аудит админ-действий.

Только новые таблицы (expand), без изменения существующих. GIN-индекс ``gin_trgm_ops``
требует расширения ``pg_trgm`` — его создаёт init-скрипт БД (у роли сервиса нет прав
на CREATE EXTENSION), поэтому здесь расширение не создаётся.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09
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
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name=op.f("uq_admin_actions_actor_id_idempotency_key")
        ),
    )
    op.create_index("ix_admin_actions_target_id", "admin_actions", ["target_id"], unique=False)
    op.create_table(
        "genres",
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("slug", name=op.f("pk_genres")),
    )
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=200), nullable=True),
        sa.Column("file_id", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("counters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('tmdb', 'file', 'musicbrainz', 'spotify', 'yandex_music')",
            name=op.f("ck_import_jobs_source_known"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name=op.f("ck_import_jobs_status_known"),
        ),
        sa.CheckConstraint(
            "(ref IS NULL) <> (file_id IS NULL)", name=op.f("ck_import_jobs_ref_xor_file")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
        sa.UniqueConstraint(
            "actor_id", "idempotency_key", name=op.f("uq_import_jobs_actor_id_idempotency_key")
        ),
    )
    op.create_table(
        "media_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("original_title", sa.String(length=300), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("subtitle", sa.String(length=300), nullable=True),
        sa.Column("popularity", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("blocked_reason_code", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'track' AND domain = 'music') OR (kind = 'film' AND domain = 'film')",
            name=op.f("ck_media_items_kind_matches_domain"),
        ),
        sa.CheckConstraint(
            "(status = 'blocked') = (blocked_reason_code IS NOT NULL)",
            name=op.f("ck_media_items_blocked_has_reason"),
        ),
        sa.CheckConstraint("domain IN ('music', 'film')", name=op.f("ck_media_items_domain_known")),
        sa.CheckConstraint("kind IN ('track', 'film')", name=op.f("ck_media_items_kind_known")),
        sa.CheckConstraint(
            "status IN ('active', 'blocked')", name=op.f("ck_media_items_status_known")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_media_items_version_positive")),
        sa.CheckConstraint(
            "year IS NULL OR year BETWEEN 1870 AND 2100", name=op.f("ck_media_items_year_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_media_items")),
    )
    op.create_table(
        "persons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_key", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_persons")),
        sa.UniqueConstraint("external_key", name=op.f("uq_persons_external_key")),
    )
    op.create_table(
        "aliases",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("normalized", sa.String(length=300), nullable=False),
        sa.Column("text", sa.String(length=300), nullable=False),
        sa.Column("lang", sa.String(length=8), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('title', 'alt', 'translit', 'short')", name=op.f("ck_aliases_kind_known")
        ),
        sa.CheckConstraint("normalized <> ''", name=op.f("ck_aliases_normalized_not_empty")),
        sa.CheckConstraint("source IN ('import', 'manual')", name=op.f("ck_aliases_source_known")),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_aliases_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_item_id", "normalized", name=op.f("pk_aliases")),
    )
    op.create_index(
        "ix_aliases_normalized_trgm",
        "aliases",
        ["normalized"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )
    op.create_table(
        "external_refs",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "source IN ('tmdb', 'musicbrainz', 'isrc', 'spotify', 'yandex_music', 'seed')",
            name=op.f("ck_external_refs_source_known"),
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_external_refs_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source", "external_id", name=op.f("pk_external_refs")),
    )
    op.create_index(
        "ix_external_refs_media_item_id", "external_refs", ["media_item_id"], unique=False
    )
    op.create_table(
        "media_item_clue_status",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("clue_type", sa.String(length=64), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("asset_set_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("levels_count", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'failed')", name=op.f("ck_media_item_clue_status_status_known")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_media_item_clue_status_version_positive")),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_media_item_clue_status_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "media_item_id", "clue_type", "profile_key", name=op.f("pk_media_item_clue_status")
        ),
    )
    op.create_table(
        "media_item_genres",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("genre_slug", sa.String(length=64), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["genre_slug"], ["genres.slug"], name=op.f("fk_media_item_genres_genre_slug_genres")
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_media_item_genres_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_item_id", "genre_slug", name=op.f("pk_media_item_genres")),
    )
    op.create_table(
        "media_item_persons",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "role IN ('artist', 'director', 'actor')", name=op.f("ck_media_item_persons_role_known")
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_media_item_persons_position_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_media_item_persons_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"], ["persons.id"], name=op.f("fk_media_item_persons_person_id_persons")
        ),
        sa.PrimaryKeyConstraint(
            "media_item_id", "person_id", "role", name=op.f("pk_media_item_persons")
        ),
    )
    op.create_index(
        "ix_media_item_persons_person_id", "media_item_persons", ["person_id"], unique=False
    )
    op.create_table(
        "media_metrics",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_media_metrics_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_item_id", name=op.f("pk_media_metrics")),
    )
    op.create_table(
        "source_media_refs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("uri", sa.String(length=2000), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('audio', 'frame', 'poster')", name=op.f("ck_source_media_refs_kind_known")
        ),
        sa.CheckConstraint(
            "origin IN ('upload', 'tmdb')", name=op.f("ck_source_media_refs_origin_known")
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_source_media_refs_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_media_refs")),
    )
    op.create_index(
        "ix_source_media_refs_media_item_id", "source_media_refs", ["media_item_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_source_media_refs_media_item_id", table_name="source_media_refs")
    op.drop_table("source_media_refs")
    op.drop_table("media_metrics")
    op.drop_index("ix_media_item_persons_person_id", table_name="media_item_persons")
    op.drop_table("media_item_persons")
    op.drop_table("media_item_genres")
    op.drop_table("media_item_clue_status")
    op.drop_index("ix_external_refs_media_item_id", table_name="external_refs")
    op.drop_table("external_refs")
    op.drop_index(
        "ix_aliases_normalized_trgm",
        table_name="aliases",
        postgresql_using="gin",
        postgresql_ops={"normalized": "gin_trgm_ops"},
    )
    op.drop_table("aliases")
    op.drop_table("persons")
    op.drop_table("media_items")
    op.drop_table("import_jobs")
    op.drop_table("genres")
    op.drop_index("ix_admin_actions_target_id", table_name="admin_actions")
    op.drop_table("admin_actions")
