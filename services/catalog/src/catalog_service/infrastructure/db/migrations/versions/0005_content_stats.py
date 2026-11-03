"""Статистика угадываний по контенту (ADR 0076).

Только расширение (expand): две новые таблицы, существующие не меняются.

* ``content_mode_stats`` — счётчики по ``(media_item_id, mode_key)``: показы, исходы,
  сумма и число времён точных ответов;
* ``content_mode_buckets`` — гистограммы по уровню (``level``) и времени (``time``) точного
  ответа; число строк ограничено настройкой ``GV_STATS__TIME_BUCKET_MAX``.

Откат удаляет обе таблицы вместе со статистикой (она восстанавливается только новыми
событиями), поэтому перед откатом данные можно выгрузить.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    zero = sa.text("0")
    op.create_table(
        "content_mode_stats",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("shown", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("exact", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("partial", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("miss", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("gave_up", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("time_sum_ms", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column("time_count", sa.BigInteger(), server_default=zero, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "shown >= 0 AND exact >= 0 AND partial >= 0 AND miss >= 0 AND gave_up >= 0"
            " AND time_sum_ms >= 0 AND time_count >= 0",
            name=op.f("ck_content_mode_stats_counters_non_negative"),
        ),
        sa.CheckConstraint(
            "exact + partial + miss + gave_up = shown",
            name=op.f("ck_content_mode_stats_outcomes_sum_to_shown"),
        ),
        sa.CheckConstraint(
            "time_count <= exact", name=op.f("ck_content_mode_stats_time_count_within_exact")
        ),
        sa.PrimaryKeyConstraint("media_item_id", "mode_key", name=op.f("pk_content_mode_stats")),
    )
    op.create_index("ix_content_mode_stats_mode_shown", "content_mode_stats", ["mode_key", "shown"])
    op.create_table(
        "content_mode_buckets",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("bucket", sa.Integer(), nullable=False),
        sa.Column("n", sa.BigInteger(), server_default=zero, nullable=False),
        sa.CheckConstraint(
            "kind IN ('level', 'time')", name=op.f("ck_content_mode_buckets_kind_known")
        ),
        sa.CheckConstraint(
            "bucket >= 0 AND n >= 0", name=op.f("ck_content_mode_buckets_non_negative")
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id", "mode_key"],
            ["content_mode_stats.media_item_id", "content_mode_stats.mode_key"],
            name=op.f("fk_content_mode_buckets_media_item_id_content_mode_stats"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "media_item_id", "mode_key", "kind", "bucket", name=op.f("pk_content_mode_buckets")
        ),
    )


def downgrade() -> None:
    op.drop_table("content_mode_buckets")
    op.drop_index("ix_content_mode_stats_mode_shown", table_name="content_mode_stats")
    op.drop_table("content_mode_stats")
