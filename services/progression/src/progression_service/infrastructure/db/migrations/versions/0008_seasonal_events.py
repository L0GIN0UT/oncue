"""Сезонные события (этап 5d, поток E1; ADR 0054-0056).

Только новые таблицы (expand): ``events`` (определение), ``event_runs`` (запуски со снимком),
``event_find_progress`` (прогресс и открытие находок), ``event_completions`` (закрытие события).
Внешние ключи ``RESTRICT``. Откат прерывается ошибкой, если есть события, запуски, прогресс,
закрытия или проводки награды событий (``op_id LIKE 'event:%'``): деньги и достижения игроков
восстановить нельзя.

Цепочка ревизий: ``0007`` → ``0008`` (эта).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # Предмет только для награды события: без цены и уровня. Условие открытия ослабляется
    # (ещё один допустимый вариант), поэтому существующие строки проходят новую проверку.
    op.add_column("items", sa.Column("event_key", sa.String(length=64), nullable=True))
    op.drop_constraint(op.f("ck_items_unlock_condition_required"), "items", type_="check")
    op.create_check_constraint(
        op.f("ck_items_unlock_condition_required"),
        "items",
        "level_required IS NOT NULL OR price IS NOT NULL OR event_key IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_items_event_item_not_sold"),
        "items",
        "event_key IS NULL OR (level_required IS NULL AND price IS NULL)",
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("map_key", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_events_key_shape")),
        sa.CheckConstraint("body_sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_events_sha_shape")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
        sa.UniqueConstraint("key", name=op.f("uq_events_key")),
    )
    op.create_table(
        "event_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("starts_at", _TS, nullable=False),
        sa.Column("ends_at", _TS, nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("body_sha256", sa.String(length=64), nullable=True),
        sa.Column("published_at", _TS, nullable=True),
        sa.Column("stopped_at", _TS, nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('draft','published','active','finished','stopped')",
            name=op.f("ck_event_runs_status_known"),
        ),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_event_runs_period_ordered")),
        sa.CheckConstraint(
            "status = 'draft' OR (snapshot IS NOT NULL AND body_sha256 IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name=op.f("ck_event_runs_published_has_snapshot"),
        ),
        sa.CheckConstraint(
            "(status = 'stopped') = (stopped_at IS NOT NULL)",
            name=op.f("ck_event_runs_stopped_dated"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name=op.f("fk_event_runs_event_id_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_runs")),
        sa.UniqueConstraint("event_id", "seq", name=op.f("uq_event_runs_event_id_seq")),
    )
    op.create_index("ix_event_runs_status_starts", "event_runs", ["status", "starts_at"])
    op.create_table(
        "event_find_progress",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("find_key", sa.String(length=64), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("unlocked_at", _TS, nullable=True),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint(
            "progress >= 0", name=op.f("ck_event_find_progress_progress_non_negative")
        ),
        sa.CheckConstraint(
            "period_key = 'event' OR period_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'",
            name=op.f("ck_event_find_progress_period_key_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["event_runs.id"],
            name=op.f("fk_event_find_progress_run_id_event_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "run_id", "user_id", "find_key", "period_key", name=op.f("pk_event_find_progress")
        ),
    )
    op.create_index(
        "ux_event_find_progress_unlocked",
        "event_find_progress",
        ["run_id", "user_id", "find_key"],
        unique=True,
        postgresql_where=sa.text("unlocked_at IS NOT NULL"),
    )
    op.create_index("ix_event_find_progress_user", "event_find_progress", ["user_id", "run_id"])
    op.create_table(
        "event_completions",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("completed_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["event_runs.id"],
            name=op.f("fk_event_completions_run_id_event_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("run_id", "user_id", name=op.f("pk_event_completions")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    counts = {
        table: bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
        for table in ("events", "event_runs", "event_find_progress", "event_completions")
    }
    ledger = sum(
        bind.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE op_id LIKE 'event:%'")  # noqa: S608
        ).scalar_one()
        for table in ("xp_ledger", "currency_ledger", "accrual_marks")
    )
    event_items = bind.execute(
        sa.text("SELECT count(*) FROM items WHERE event_key IS NOT NULL")
    ).scalar_one()
    if any(counts.values()) or ledger or event_items:
        raise RuntimeError(
            f"откат 0008 запрещён: данные событий {counts}, проводок/отметок награды {ledger}, "
            f"предметов событий {event_items}"
        )
    op.drop_constraint(op.f("ck_items_event_item_not_sold"), "items", type_="check")
    op.drop_constraint(op.f("ck_items_unlock_condition_required"), "items", type_="check")
    op.create_check_constraint(
        op.f("ck_items_unlock_condition_required"),
        "items",
        "level_required IS NOT NULL OR price IS NOT NULL",
    )
    op.drop_column("items", "event_key")
    op.drop_table("event_completions")
    op.drop_index("ix_event_find_progress_user", table_name="event_find_progress")
    op.drop_index("ux_event_find_progress_unlocked", table_name="event_find_progress")
    op.drop_table("event_find_progress")
    op.drop_index("ix_event_runs_status_starts", table_name="event_runs")
    op.drop_table("event_runs")
    op.drop_table("events")
