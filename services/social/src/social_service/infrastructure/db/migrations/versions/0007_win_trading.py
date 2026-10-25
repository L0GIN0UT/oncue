"""Детектор win-trading: факты дуэлей и сигналы для модератора (ADR 0068).

Только новые таблицы (expand): существующие не меняются, блокировок на них нет; новые таблицы
пусты, индексы создаются мгновенно. Старые матчи не восстанавливаются (события уже прошли).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "win_trading_matches",
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("user_low", sa.Uuid(), nullable=False),
        sa.Column("user_high", sa.Uuid(), nullable=False),
        sa.Column("winner_id", sa.Uuid(), nullable=True),
        sa.Column("forfeiter_id", sa.Uuid(), nullable=True),
        sa.Column("finished_at", _TS, nullable=False),
        sa.CheckConstraint(
            "user_low < user_high", name=op.f("ck_win_trading_matches_pair_ordered")
        ),
        sa.PrimaryKeyConstraint("match_id", name=op.f("pk_win_trading_matches")),
    )
    op.create_index(
        "ix_win_trading_matches_pair",
        "win_trading_matches",
        ["user_low", "user_high", "finished_at"],
        unique=False,
    )
    op.create_index(
        "ix_win_trading_matches_high",
        "win_trading_matches",
        ["user_high", "finished_at"],
        unique=False,
    )
    op.create_index(
        "ix_win_trading_matches_forfeiter",
        "win_trading_matches",
        ["forfeiter_id", "finished_at"],
        unique=False,
        postgresql_where=sa.text("forfeiter_id IS NOT NULL"),
    )
    op.create_table(
        "win_trading_signals",
        sa.Column("signal_key", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("other_user_id", sa.Uuid(), nullable=True),
        sa.Column("match_count", sa.Integer(), nullable=False),
        sa.Column("streak", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("window_s", sa.Integer(), nullable=False),
        sa.Column("first_at", _TS, nullable=False),
        sa.Column("last_at", _TS, nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint(
            "kind IN ('pair_repeat', 'forfeit_series', 'forfeit_window')",
            name=op.f("ck_win_trading_signals_kind_known"),
        ),
        sa.CheckConstraint(
            "severity IN ('medium', 'high')", name=op.f("ck_win_trading_signals_severity_known")
        ),
        sa.PrimaryKeyConstraint("signal_key", name=op.f("pk_win_trading_signals")),
    )
    op.create_index(
        "ix_win_trading_signals_queue", "win_trading_signals", ["last_at", "signal_key"]
    )
    op.create_index("ix_win_trading_signals_user", "win_trading_signals", ["user_id"])
    op.create_index("ix_win_trading_signals_other", "win_trading_signals", ["other_user_id"])


def downgrade() -> None:
    op.drop_table("win_trading_signals")
    op.drop_table("win_trading_matches")
