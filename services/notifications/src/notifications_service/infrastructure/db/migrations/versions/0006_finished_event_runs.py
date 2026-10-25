"""Завершённые запуски событий (остановка массовой рассылки, ADR 0054).

Expand: новая таблица, существующие не меняются; код прежней версии её не знает и не ломается.
Откат удаляет таблицу (данные восстановимы повтором ``EventRunFinished``).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "finished_event_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IN ('ended', 'stopped')", name=op.f("ck_finished_event_runs_reason_known")
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_finished_event_runs")),
    )


def downgrade() -> None:
    op.drop_table("finished_event_runs")
