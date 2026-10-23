"""Ядро rating: ladder-ы и версии, сезоны, рейтинги, история, топ недели, проекции (этап 4b, G2).

Только новые таблицы (expand), ``outbox``/``inbox`` не меняются; таблицы пустые, индексы
создаются обычным ``CREATE INDEX``. ``rating_history`` — ``PARTITION BY LIST (season_id)``:
партицию сезона создаёт код вместе с сезоном, здесь — только ``DEFAULT`` (страховка от потери
строки). Откат удаляет только эти таблицы (с партициями).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
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
    op.create_table(
        "ladders",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint("domain IN ('music','film')", name=op.f("ck_ladders_domain_known")),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_ladders")),
    )
    op.create_table(
        "ladder_versions",
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config", _JSONB, nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("published_at", _TS, nullable=True),
        sa.CheckConstraint("version >= 1", name=op.f("ck_ladder_versions_version_positive")),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')",
            name=op.f("ck_ladder_versions_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["ladder"],
            ["ladders.key"],
            name=op.f("fk_ladder_versions_ladder_ladders"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ladder", "version", name=op.f("pk_ladder_versions")),
    )
    op.create_index(
        "uq_ladder_versions_draft",
        "ladder_versions",
        ["ladder"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_ladder_versions_published",
        "ladder_versions",
        ["ladder"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "seasons",
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("ladder_version", sa.Integer(), nullable=False),
        sa.Column("starts_at", _TS, nullable=False),
        sa.Column("ends_at", _TS, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("finished_at", _TS, nullable=True),
        sa.Column("closed_by", sa.String(length=16), nullable=True),
        sa.Column("players_count", sa.Integer(), nullable=True),
        sa.CheckConstraint("number >= 1", name=op.f("ck_seasons_number_positive")),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_seasons_period_ordered")),
        sa.CheckConstraint(
            "status IN ('scheduled','active','finished')", name=op.f("ck_seasons_status_known")
        ),
        sa.CheckConstraint(
            "closed_by IS NULL OR closed_by IN ('schedule','admin')",
            name=op.f("ck_seasons_closed_by_known"),
        ),
        sa.ForeignKeyConstraint(
            ["ladder"], ["ladders.key"], name=op.f("fk_seasons_ladder_ladders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("season_id", name=op.f("pk_seasons")),
        sa.UniqueConstraint("ladder", "number", name=op.f("uq_seasons_ladder_number")),
    )
    op.create_index(
        "uq_seasons_active",
        "seasons",
        ["ladder"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_seasons_ladder_starts_at", "seasons", ["ladder", "starts_at"], unique=False)
    op.create_table(
        "ratings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("matches_total", sa.Integer(), nullable=False),
        sa.Column("season_matches", sa.Integer(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint(
            "season_matches >= 0 AND matches_total >= season_matches",
            name=op.f("ck_ratings_matches_ok"),
        ),
        sa.CheckConstraint(
            "event_version >= 0", name=op.f("ck_ratings_event_version_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["season_id"], ["seasons.season_id"], name=op.f("fk_ratings_season_id_seasons")
        ),
        sa.PrimaryKeyConstraint("user_id", "ladder", "season_id", name=op.f("pk_ratings")),
    )
    op.create_index("ix_ratings_season_rating", "ratings", ["season_id", "rating"], unique=False)
    op.create_table(
        "rating_history",
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("queue_format", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=False),
        sa.Column("old", sa.Integer(), nullable=False),
        sa.Column("new", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("old_tier", sa.String(length=64), nullable=True),
        sa.Column("tier", sa.String(length=64), nullable=True),
        sa.Column("calibrating", sa.Boolean(), nullable=False),
        sa.Column("calibration_left", sa.Integer(), nullable=False),
        sa.Column("matches_played", sa.Integer(), nullable=False),
        sa.Column("leaver_penalty", sa.Integer(), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('win','loss','draw')", name=op.f("ck_rating_history_outcome_known")
        ),
        sa.CheckConstraint(
            "leaver_penalty >= 0", name=op.f("ck_rating_history_penalty_non_negative")
        ),
        sa.PrimaryKeyConstraint("season_id", "user_id", "match_id", name=op.f("pk_rating_history")),
        postgresql_partition_by="LIST (season_id)",
    )
    op.execute("CREATE TABLE rating_history_default PARTITION OF rating_history DEFAULT")
    op.create_index(
        "ix_rating_history_user",
        "rating_history",
        ["user_id", "ladder", "at", "match_id"],
        unique=False,
    )
    op.create_index("ix_rating_history_match", "rating_history", ["match_id"], unique=False)
    op.create_table(
        "weekly_deltas",
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("ladder", "week_start", "user_id", name=op.f("pk_weekly_deltas")),
    )
    op.create_index(
        "ix_weekly_deltas_top", "weekly_deltas", ["ladder", "week_start", "delta"], unique=False
    )
    op.create_table(
        "weekly_tops",
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=True),
        sa.Column("top", _JSONB, nullable=False),
        sa.Column("finalized_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("ladder", "week_start", name=op.f("pk_weekly_tops")),
    )
    op.create_table(
        "processed_matches",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("processed_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("match_id", name=op.f("pk_processed_matches")),
    )
    op.create_table(
        "users_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=True),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users_proj")),
    )
    op.create_table(
        "rebuild_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=False),
        sa.Column("season_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("done_at", _TS, nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rebuild_requests")),
    )
    op.create_index(
        "ix_rebuild_requests_pending",
        "rebuild_requests",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("done_at IS NULL"),
    )
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
    )
    op.create_index(
        "ix_admin_actions_target_created_at",
        "admin_actions",
        ["target", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    for table in (
        "admin_actions",
        "rebuild_requests",
        "users_proj",
        "processed_matches",
        "weekly_tops",
        "weekly_deltas",
        "rating_history",
        "ratings",
        "seasons",
        "ladder_versions",
        "ladders",
        "idempotency_keys",
    ):
        op.drop_table(table)
