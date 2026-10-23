"""Задания, бейджи, колесо, бусты, журнал выдач предметов (этап 5a, поток P2).

Только новые таблицы (expand): таблицы P1, ``outbox`` и ``inbox`` не меняются. Таблицы пустые,
индексы создаются обычным ``CREATE INDEX``. Денежных журналов здесь нет (проводки Версов и опыта
остаются в ``currency_ledger``/``xp_ledger`` из ``0002``), поэтому откат удаляет таблицы без
проверок: потерять можно прогресс заданий, бейджи, историю спинов и бустов, но не баланс.
``grants_log`` - заглушка выдачи косметики (5b заменит реальной выдачей инвентаря).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "quest_assignments",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("quest_key", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=True),
        sa.Column("reward_xp", sa.BigInteger(), nullable=False),
        sa.Column("reward_verses", sa.BigInteger(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("assigned_at", _TS, nullable=False),
        sa.CheckConstraint(
            "period IN ('daily','weekly','one_time')",
            name=op.f("ck_quest_assignments_period_known"),
        ),
        sa.CheckConstraint("target >= 1", name=op.f("ck_quest_assignments_target_positive")),
        sa.CheckConstraint(
            "reward_xp >= 0 AND reward_verses >= 0",
            name=op.f("ck_quest_assignments_reward_non_negative"),
        ),
        sa.CheckConstraint(
            "domain IS NULL OR domain IN ('music','film')",
            name=op.f("ck_quest_assignments_domain_known"),
        ),
        sa.ForeignKeyConstraint(
            ["config_version"],
            ["config_versions.version"],
            name=op.f("fk_quest_assignments_config_version_config_versions"),
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "quest_key", "period_start", name=op.f("pk_quest_assignments")
        ),
    )
    op.create_index(
        "ix_quest_assignments_user_period",
        "quest_assignments",
        ["user_id", "period", "period_start"],
        unique=False,
    )
    op.create_index(
        "uq_quest_assignments_one_time",
        "quest_assignments",
        ["user_id", "quest_key"],
        unique=True,
        postgresql_where=sa.text("period = 'one_time'"),
    )
    op.create_table(
        "quest_progress",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("quest_key", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", _TS, nullable=True),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint("progress >= 0", name=op.f("ck_quest_progress_progress_non_negative")),
        sa.ForeignKeyConstraint(
            ["user_id", "quest_key", "period_start"],
            [
                "quest_assignments.user_id",
                "quest_assignments.quest_key",
                "quest_assignments.period_start",
            ],
            name=op.f("fk_quest_progress_user_id_quest_assignments"),
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "quest_key", "period_start", name=op.f("pk_quest_progress")
        ),
    )
    op.create_table(
        "badges_unlocked",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("badge_key", sa.String(length=64), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("unlocked_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["config_version"],
            ["config_versions.version"],
            name=op.f("fk_badges_unlocked_config_version_config_versions"),
        ),
        sa.PrimaryKeyConstraint("user_id", "badge_key", name=op.f("pk_badges_unlocked")),
    )
    op.create_table(
        "metric_counters",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint("value >= 0", name=op.f("ck_metric_counters_value_non_negative")),
        sa.PrimaryKeyConstraint("user_id", "metric", name=op.f("pk_metric_counters")),
    )
    op.create_table(
        "metric_marks",
        sa.Column("subject_key", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("subject_key", name=op.f("pk_metric_marks")),
    )
    op.create_table(
        "wheel_spins",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sector_key", sa.String(length=64), nullable=False),
        sa.Column("prize_kind", sa.String(length=8), nullable=False),
        sa.Column("prize_amount", sa.BigInteger(), nullable=False),
        sa.Column("prize_multiplier", sa.Numeric(precision=8, scale=3), nullable=True),
        sa.Column("prize_matches", sa.Integer(), nullable=True),
        sa.Column("prize_item_key", sa.String(length=64), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("spun_at", _TS, nullable=False),
        sa.CheckConstraint(
            "prize_kind IN ('xp','verses','boost','item','none')",
            name=op.f("ck_wheel_spins_prize_kind_known"),
        ),
        sa.CheckConstraint(
            "prize_amount >= 0", name=op.f("ck_wheel_spins_prize_amount_non_negative")
        ),
        sa.CheckConstraint(
            "prize_kind <> 'boost' OR (prize_multiplier > 1 AND prize_matches >= 1)",
            name=op.f("ck_wheel_spins_boost_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["config_version"],
            ["config_versions.version"],
            name=op.f("fk_wheel_spins_config_version_config_versions"),
        ),
        sa.PrimaryKeyConstraint("user_id", "day", name=op.f("pk_wheel_spins")),
    )
    op.create_table(
        "boosts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("multiplier", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("matches_left", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("queued_at", _TS, nullable=False),
        sa.CheckConstraint("multiplier > 1", name=op.f("ck_boosts_multiplier_above_one")),
        sa.CheckConstraint("matches_left >= 0", name=op.f("ck_boosts_matches_left_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boosts")),
        sa.UniqueConstraint("user_id", "source_ref", name=op.f("uq_boosts_user_source_ref")),
    )
    op.create_index(
        "ix_boosts_user_live",
        "boosts",
        ["user_id", "queued_at", "id"],
        unique=False,
        postgresql_where=sa.text("matches_left > 0"),
    )
    op.create_table(
        "boost_spends",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("boost_id", sa.Uuid(), nullable=False),
        sa.Column("multiplier", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.CheckConstraint("multiplier > 1", name=op.f("ck_boost_spends_multiplier_above_one")),
        sa.ForeignKeyConstraint(
            ["boost_id"], ["boosts.id"], name=op.f("fk_boost_spends_boost_id_boosts")
        ),
        sa.PrimaryKeyConstraint("user_id", "match_id", name=op.f("pk_boost_spends")),
    )
    op.create_table(
        "grants_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("item_key", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("at", _TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','granted')", name=op.f("ck_grants_log_status_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grants_log")),
        sa.UniqueConstraint("source", "source_ref", name=op.f("uq_grants_log_source_ref")),
    )
    op.create_index("ix_grants_log_user", "grants_log", ["user_id", "at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_grants_log_user", table_name="grants_log")
    op.drop_table("grants_log")
    op.drop_table("boost_spends")
    op.drop_index(
        "ix_boosts_user_live",
        table_name="boosts",
        postgresql_where=sa.text("matches_left > 0"),
    )
    op.drop_table("boosts")
    op.drop_table("wheel_spins")
    op.drop_table("metric_marks")
    op.drop_table("metric_counters")
    op.drop_table("badges_unlocked")
    op.drop_table("quest_progress")
    op.drop_index(
        "uq_quest_assignments_one_time",
        table_name="quest_assignments",
        postgresql_where=sa.text("period = 'one_time'"),
    )
    op.drop_index("ix_quest_assignments_user_period", table_name="quest_assignments")
    op.drop_table("quest_assignments")
