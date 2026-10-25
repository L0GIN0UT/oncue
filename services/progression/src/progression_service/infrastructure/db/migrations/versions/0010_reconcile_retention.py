"""Инкрементальная сверка и ретенция (expand-only; ADR 0062).

* новая таблица ``reconcile_checkpoints`` (по строке на журнал): контрольная точка сверки;
  журналы не меняются. Создание пустой таблицы берёт только блокировку на саму новую таблицу;
* индексы ``ix_metric_marks_at`` и ``ix_quest_progress_period_start`` строятся ``CONCURRENTLY``
  вне транзакции (не блокируют запись в горячие таблицы начислений; ``IF NOT EXISTS`` делает
  повтор после оборванной постройки безопасным - невалидный индекс нужно удалить вручную).

Права рантайм-роли ``progression_app`` (ADR 0060) выдаются ``ALTER DEFAULT PRIVILEGES`` из
``0009`` и повторной выдачей job ``migrate``; таблица не журнал, ей нужны UPDATE/DELETE.
Откат удаляет только эти объекты (данные точек пересоздаются полным проходом).

Цепочка ревизий: ``0009`` -> ``0010`` (эта).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "reconcile_checkpoints",
        sa.Column("ledger", sa.String(length=32), nullable=False),
        sa.Column("last_id", sa.Uuid(), nullable=True),
        sa.Column("last_full_at", _TS, nullable=True),
        sa.Column("full_mismatches", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint(
            "full_mismatches >= 0",
            name=op.f("ck_reconcile_checkpoints_full_mismatches_non_negative"),
        ),
        sa.PrimaryKeyConstraint("ledger", name=op.f("pk_reconcile_checkpoints")),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_metric_marks_at",
            "metric_marks",
            ["at"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_quest_progress_period_start",
            "quest_progress",
            ["period_start"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_quest_progress_period_start",
            table_name="quest_progress",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_metric_marks_at",
            table_name="metric_marks",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_table("reconcile_checkpoints")
