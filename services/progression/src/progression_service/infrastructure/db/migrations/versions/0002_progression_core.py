"""Ядро progression: версии конфига, прогресс, журналы опыта и Версов, балансы, счётчики.

Только новые таблицы (expand): ``outbox``/``inbox`` не меняются.
``accrual_marks`` - нулевые отметки предметов (кодом не удаляются). Таблицы пустые, индексы
создаются обычным ``CREATE INDEX``. Журналы ``xp_ledger`` и ``currency_ledger`` append-only:
триггеры ``BEFORE UPDATE OR DELETE`` (по строкам) и ``BEFORE TRUNCATE`` отвечают ошибкой — защита
от порчи журнала даже у роли с правами на таблицу (ADR 0043). Откат удаляет только эти таблицы
и функцию триггера; если в журналах уже есть проводки, он прерывается ошибкой — финансовые данные
молча не удаляются (нужна осознанная ручная процедура).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_LEDGERS = ("xp_ledger", "currency_ledger")

_FORBID_FUNCTION = """
CREATE FUNCTION progression_ledger_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'журнал % append-only: % запрещён', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$
"""


def _ledger_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("op_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("reversal_of", sa.String(length=200), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("at", _TS, nullable=False),
    ]


def _ledger_constraints(name: str) -> list[sa.schema.SchemaItem]:
    return [
        sa.CheckConstraint("amount > 0", name=op.f(f"ck_{name}_amount_positive")),
        sa.CheckConstraint(
            "direction IN ('credit','debit')", name=op.f(f"ck_{name}_direction_known")
        ),
        sa.CheckConstraint(
            "reversal_of IS NULL OR reversal_of <> op_id", name=op.f(f"ck_{name}_reversal_not_self")
        ),
        sa.ForeignKeyConstraint(
            ["config_version"],
            ["config_versions.version"],
            name=op.f(f"fk_{name}_config_version_config_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["reversal_of"], [f"{name}.op_id"], name=op.f(f"fk_{name}_reversal_of_{name}")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{name}")),
        sa.UniqueConstraint("op_id", name=op.f(f"uq_{name}_op_id")),
        sa.UniqueConstraint("reversal_of", name=op.f(f"uq_{name}_reversal_of")),
    ]


def upgrade() -> None:
    op.create_table(
        "config_versions",
        sa.Column("version", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("body", _JSONB, nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("published_at", _TS, nullable=True),
        sa.CheckConstraint("version >= 1", name=op.f("ck_config_versions_version_positive")),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')",
            name=op.f("ck_config_versions_status_known"),
        ),
        sa.CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL",
            name=op.f("ck_config_versions_published_dated"),
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_config_versions")),
    )
    op.create_index(
        "uq_config_versions_published",
        "config_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_table(
        "progress",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("total_xp", sa.BigInteger(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("total_xp >= 0", name=op.f("ck_progress_total_xp_non_negative")),
        sa.CheckConstraint("level >= 1", name=op.f("ck_progress_level_positive")),
        sa.ForeignKeyConstraint(
            ["config_version"],
            ["config_versions.version"],
            name=op.f("fk_progress_config_version_config_versions"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_progress")),
    )
    op.create_table(
        "xp_ledger",
        *_ledger_columns(),
        sa.Column("base_amount", sa.BigInteger(), nullable=False),
        sa.Column("boost_multiplier", sa.Numeric(precision=8, scale=3), nullable=False),
        sa.CheckConstraint("base_amount > 0", name=op.f("ck_xp_ledger_base_amount_positive")),
        sa.CheckConstraint("boost_multiplier > 0", name=op.f("ck_xp_ledger_boost_positive")),
        *_ledger_constraints("xp_ledger"),
    )
    op.create_index("ix_xp_ledger_user_at", "xp_ledger", ["user_id", "at", "id"], unique=False)
    op.create_table(
        "currency_ledger",
        *_ledger_columns(),
        sa.Column("bucket", sa.String(length=8), nullable=False),
        sa.CheckConstraint(
            "bucket IN ('earned','paid')", name=op.f("ck_currency_ledger_bucket_known")
        ),
        *_ledger_constraints("currency_ledger"),
    )
    op.create_index(
        "ix_currency_ledger_user_at", "currency_ledger", ["user_id", "at", "id"], unique=False
    )
    op.create_table(
        "balances",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("bucket", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint("amount >= 0", name=op.f("ck_balances_amount_non_negative")),
        sa.CheckConstraint("bucket IN ('earned','paid')", name=op.f("ck_balances_bucket_known")),
        sa.PrimaryKeyConstraint("user_id", "bucket", name=op.f("pk_balances")),
    )
    op.create_table(
        "daily_counters",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("solo_xp", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("solo_verses", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("online_verses", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "first_win_awarded", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.CheckConstraint(
            "solo_xp >= 0 AND solo_verses >= 0 AND online_verses >= 0",
            name=op.f("ck_daily_counters_counters_non_negative"),
        ),
        sa.PrimaryKeyConstraint("user_id", "day", name=op.f("pk_daily_counters")),
    )
    op.create_table(
        "daily_streaks",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("current", sa.Integer(), nullable=False),
        sa.Column("best", sa.Integer(), nullable=False),
        sa.Column("last_day", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "domain IN ('music','film')", name=op.f("ck_daily_streaks_domain_known")
        ),
        sa.CheckConstraint(
            "current >= 0 AND best >= current", name=op.f("ck_daily_streaks_streak_ordered")
        ),
        sa.PrimaryKeyConstraint("user_id", "domain", name=op.f("pk_daily_streaks")),
    )
    op.create_table(
        "reward_periods",
        sa.Column("period_key", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("awarded_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("period_key", "user_id", name=op.f("pk_reward_periods")),
    )

    op.create_table(
        "accrual_marks",
        sa.Column("op_id", sa.String(length=200), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("op_id", name=op.f("pk_accrual_marks")),
    )

    op.execute(_FORBID_FUNCTION)
    for table in _LEDGERS:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION progression_ledger_forbid_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION progression_ledger_forbid_mutation()"
        )


def _refuse_if_ledgers_have_rows() -> None:
    """Прервать откат, если в журналах есть строки (деньги и опыт не удаляются молча)."""
    bind = op.get_bind()
    for table in _LEDGERS:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608 — имя из константы _LEDGERS
        if count:
            raise RuntimeError(
                f"откат 0002 запрещён: в журнале {table} {count} проводок; "
                "финансовые данные не удаляются автоматически"
            )


def downgrade() -> None:
    _refuse_if_ledgers_have_rows()
    for table in _LEDGERS:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.drop_table("accrual_marks")
    op.drop_table("reward_periods")
    op.drop_table("daily_streaks")
    op.drop_table("daily_counters")
    op.drop_table("balances")
    op.drop_index("ix_currency_ledger_user_at", table_name="currency_ledger")
    op.drop_table("currency_ledger")
    op.drop_index("ix_xp_ledger_user_at", table_name="xp_ledger")
    op.drop_table("xp_ledger")
    op.drop_table("progress")
    op.drop_index(
        "uq_config_versions_published",
        table_name="config_versions",
        postgresql_where=sa.text("status = 'published'"),
    )
    op.drop_table("config_versions")
    op.execute("DROP FUNCTION IF EXISTS progression_ledger_forbid_mutation()")
