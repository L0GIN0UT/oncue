"""Этап 3a (expand-only): метрики и персоны в проекции, наборы периода, рекорды, сетка,
запросы ассетов.

* ``catalog_items.metrics`` / ``person_ids`` — пустые значения по умолчанию: старые строки
  заполнятся при ``republish`` catalog (чек-лист выкладки, план §12), до тех пор пара и
  марафон их не видят;
* ``daily_sets.period`` = ``'day'`` для всех наборов этапа 2, ``subject_id`` — тема сетки;
* новые таблицы ``player_records``, ``score_histogram``, ``bracket_pick_stats``,
  ``asset_requests``.

Откат удаляет только добавленное: данные этапа 2 не теряются.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
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
    op.add_column(
        "catalog_items",
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "catalog_items",
        sa.Column(
            "person_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_catalog_items_person_ids", "catalog_items", ["person_ids"], postgresql_using="gin"
    )

    op.add_column(
        "daily_sets",
        sa.Column("period", sa.String(16), server_default=sa.text("'day'"), nullable=False),
    )
    op.add_column("daily_sets", sa.Column("subject_id", sa.Uuid(), nullable=True))
    op.create_check_constraint(
        op.f("ck_daily_sets_period_valid"), "daily_sets", "period IN ('day', 'week')"
    )

    op.create_table(
        "player_records",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("mode_key", sa.String(100), nullable=False),
        sa.Column("best", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "mode_key", name=op.f("pk_player_records")),
        sa.CheckConstraint("best >= 0", name=op.f("ck_player_records_best_non_negative")),
    )
    op.create_table(
        "score_histogram",
        sa.Column("mode_key", sa.String(100), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("mode_key", "score", name=op.f("pk_score_histogram")),
        sa.CheckConstraint(
            "score >= 0 AND sessions >= 0", name=op.f("ck_score_histogram_counts_valid")
        ),
    )
    op.create_table(
        "bracket_pick_stats",
        sa.Column("mode_key", sa.String(100), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("winner_id", sa.Uuid(), nullable=False),
        sa.Column("loser_id", sa.Uuid(), nullable=False),
        sa.Column("picks", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "mode_key", "period_start", "winner_id", "loser_id", name=op.f("pk_bracket_pick_stats")
        ),
        sa.CheckConstraint("picks >= 0", name=op.f("ck_bracket_pick_stats_picks_non_negative")),
        sa.CheckConstraint(
            "winner_id <> loser_id", name=op.f("ck_bracket_pick_stats_distinct_items")
        ),
    )
    op.create_table(
        "asset_requests",
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("mode_key", sa.String(100), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("clue_type", sa.String(64), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("request_id", name=op.f("pk_asset_requests")),
        sa.CheckConstraint(
            "status IN ('pending', 'sent')", name=op.f("ck_asset_requests_status_valid")
        ),
        sa.CheckConstraint(
            "length(params_hash) = 64", name=op.f("ck_asset_requests_params_hash_sha256")
        ),
        sa.CheckConstraint(
            "draft_revision >= 1", name=op.f("ck_asset_requests_draft_revision_positive")
        ),
    )
    op.create_index(
        "ix_asset_requests_mode_key_requested_at",
        "asset_requests",
        ["mode_key", "requested_at"],
    )
    op.create_index(
        "uq_asset_requests_pending_hash",
        "asset_requests",
        ["clue_type", "params_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("asset_requests")
    op.drop_table("bracket_pick_stats")
    op.drop_table("score_histogram")
    op.drop_table("player_records")
    op.drop_constraint(op.f("ck_daily_sets_period_valid"), "daily_sets", type_="check")
    op.drop_column("daily_sets", "subject_id")
    op.drop_column("daily_sets", "period")
    op.drop_index("ix_catalog_items_person_ids", table_name="catalog_items")
    op.drop_column("catalog_items", "person_ids")
    op.drop_column("catalog_items", "metrics")
