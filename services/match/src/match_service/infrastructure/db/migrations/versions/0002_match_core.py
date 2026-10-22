"""Ядро match: матчи, игроки, раунды (партиции по месяцу), оценки, проекции (этап 4a, R7).

Только новые таблицы (expand), ``outbox``/``inbox`` не меняются. Таблицы пустые: индексы
создаются обычным ``CREATE INDEX``. ``match_rounds`` — ``PARTITION BY RANGE (played_at)``:
здесь создаётся только партиция по умолчанию (страховка от вставки без партиции); месячные
партиции вперёд создаёт периодическая задача ``match.partitions.ensure`` (relay) —
миграция не зависит от даты применения. Откат удаляет только эти таблицы (с партициями).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-22
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


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", _JSONB, nullable=False),
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
        "matches",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("room_code", sa.String(length=8), nullable=False),
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("mode_version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("ranked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ladder", sa.String(length=64), nullable=True),
        sa.Column("meta", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "collection_ids",
            sa.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column("players_count", sa.SmallInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finish", sa.String(length=16), nullable=True),
        sa.Column("abort_reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("domain IN ('music','film')", name=op.f("ck_matches_domain_known")),
        sa.CheckConstraint(
            "finish IS NULL OR finish IN ('completed','forfeit','tech_win','aborted')",
            name=op.f("ck_matches_finish_known"),
        ),
        sa.CheckConstraint(
            "format IN ('duel','party','lobby')", name=op.f("ck_matches_format_known")
        ),
        sa.CheckConstraint(
            "(finish IS NULL) = (finished_at IS NULL)", name=op.f("ck_matches_finish_with_time")
        ),
        sa.CheckConstraint("mode_version >= 1", name=op.f("ck_matches_mode_version_positive")),
        sa.CheckConstraint(
            "players_count BETWEEN 2 AND 6", name=op.f("ck_matches_players_count_range")
        ),
        sa.PrimaryKeyConstraint("match_id", name=op.f("pk_matches")),
    )
    op.create_index("ix_matches_finished_at", "matches", ["finished_at"], unique=False)
    op.create_table(
        "match_players",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.SmallInteger(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("place", sa.SmallInteger(), nullable=False),
        sa.Column("outcome", sa.String(length=8), nullable=True),
        sa.Column("finish", sa.String(length=16), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "finish IN ('completed','forfeit','left','tech_win','tech_loss')",
            name=op.f("ck_match_players_finish_known"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('win','loss','draw')",
            name=op.f("ck_match_players_outcome_known"),
        ),
        sa.CheckConstraint("place BETWEEN 1 AND 6", name=op.f("ck_match_players_place_range")),
        sa.CheckConstraint("score >= 0", name=op.f("ck_match_players_score_non_negative")),
        sa.CheckConstraint("slot BETWEEN 0 AND 5", name=op.f("ck_match_players_slot_range")),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.match_id"],
            name=op.f("fk_match_players_match_id_matches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("match_id", "slot", name=op.f("pk_match_players")),
    )
    op.create_index(
        "ix_match_players_user_finished",
        "match_players",
        ["user_id", sa.literal_column("finished_at DESC"), sa.literal_column("match_id DESC")],
        unique=False,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_table(
        "match_rounds",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("round_index", sa.SmallInteger(), nullable=False),
        sa.Column("played_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("void", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("results", _JSONB, nullable=False),
        sa.CheckConstraint(
            "round_index BETWEEN 0 AND 49", name=op.f("ck_match_rounds_round_index_range")
        ),
        sa.PrimaryKeyConstraint(
            "match_id", "round_index", "played_at", name=op.f("pk_match_rounds")
        ),
        postgresql_partition_by="RANGE (played_at)",
    )
    op.execute("CREATE TABLE match_rounds_default PARTITION OF match_rounds DEFAULT")
    op.create_table(
        "player_ratings_given",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("from_id", sa.Uuid(), nullable=False),
        sa.Column("to_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('praise','report')", name=op.f("ck_player_ratings_given_kind_known")
        ),
        sa.CheckConstraint("from_id <> to_id", name=op.f("ck_player_ratings_given_not_self")),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.match_id"],
            name=op.f("fk_player_ratings_given_match_id_matches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "match_id", "from_id", "to_id", "kind", name=op.f("pk_player_ratings_given")
        ),
    )
    op.create_index(
        "ix_player_ratings_given_from", "player_ratings_given", ["from_id"], unique=False
    )
    op.create_index(
        "ix_player_ratings_given_to", "player_ratings_given", ["to_id", "created_at"], unique=False
    )
    op.create_table(
        "users_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_users_proj_version_positive")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users_proj")),
    )
    op.create_table(
        "mode_heads",
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("archived", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_mode_heads_version_positive")),
        sa.PrimaryKeyConstraint("mode_key", name=op.f("pk_mode_heads")),
    )


def downgrade() -> None:
    op.drop_table("mode_heads")
    op.drop_table("users_proj")
    op.drop_index("ix_player_ratings_given_to", table_name="player_ratings_given")
    op.drop_index("ix_player_ratings_given_from", table_name="player_ratings_given")
    op.drop_table("player_ratings_given")
    # Партиции (default и месячные от задачи) удаляются вместе с родительской таблицей.
    op.drop_table("match_rounds")
    op.drop_index(
        "ix_match_players_user_finished",
        table_name="match_players",
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.drop_table("match_players")
    op.drop_index("ix_matches_finished_at", table_name="matches")
    op.drop_table("matches")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
