"""Схема БД progression: задания, бейджи, колесо, бусты (этап 5a, поток P2; ADR 0042, 0044).

Таблицы добавлены миграцией ``0003``. Инварианты - в схеме: ключи идемпотентности (PK и UNIQUE)
не дают выдать награду, бейдж или спин дважды даже при гонке. Проводки Версов и опыта - в
журналах P1 (``tables.py``); здесь их нет.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from progression_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata


def _ts(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


quest_assignments = Table(
    "quest_assignments",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("quest_key", String(64), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("period", String(16), nullable=False),
    # Снимок конфига на момент назначения (R8): смена конфига его не меняет.
    Column("title", String(120), nullable=False),
    Column("description", String(400), nullable=False),
    Column("metric", String(64), nullable=False),
    Column("target", Integer, nullable=False),
    Column("domain", String(16), nullable=True),
    Column("reward_xp", BigInteger, nullable=False),
    Column("reward_verses", BigInteger, nullable=False),
    Column("config_version", Integer, ForeignKey("config_versions.version"), nullable=False),
    _ts("assigned_at"),
    PrimaryKeyConstraint("user_id", "quest_key", "period_start"),
    CheckConstraint("period IN ('daily','weekly','one_time')", name="period_known"),
    CheckConstraint("target >= 1", name="target_positive"),
    CheckConstraint("reward_xp >= 0 AND reward_verses >= 0", name="reward_non_negative"),
    CheckConstraint("domain IS NULL OR domain IN ('music','film')", name="domain_known"),
    Index("ix_quest_assignments_user_period", "user_id", "period", "period_start"),
    # Разовое задание назначается игроку один раз, в какой бы день его ни назначили.
    Index(
        "uq_quest_assignments_one_time",
        "user_id",
        "quest_key",
        unique=True,
        postgresql_where=text("period = 'one_time'"),
    ),
)

quest_progress = Table(
    "quest_progress",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("quest_key", String(64), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("progress", Integer, nullable=False, server_default=text("0")),
    # Награда выдана ровно тогда, когда стоит completed_at (одна транзакция).
    _ts("completed_at", nullable=True),
    _ts("updated_at"),
    PrimaryKeyConstraint("user_id", "quest_key", "period_start"),
    ForeignKeyConstraint(
        ["user_id", "quest_key", "period_start"],
        [
            "quest_assignments.user_id",
            "quest_assignments.quest_key",
            "quest_assignments.period_start",
        ],
    ),
    CheckConstraint("progress >= 0", name="progress_non_negative"),
    # Ретенция (ADR 0062): поиск закрытых периодов без полного скана.
    Index("ix_quest_progress_period_start", "period_start"),
)

badges_unlocked = Table(
    "badges_unlocked",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("badge_key", String(64), nullable=False),
    Column("config_version", Integer, ForeignKey("config_versions.version"), nullable=False),
    _ts("unlocked_at"),
    PrimaryKeyConstraint("user_id", "badge_key"),
)

metric_counters = Table(
    "metric_counters",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("metric", String(64), nullable=False),
    Column("value", BigInteger, nullable=False),
    _ts("updated_at"),
    PrimaryKeyConstraint("user_id", "metric"),
    CheckConstraint("value >= 0", name="value_non_negative"),
)

metric_marks = Table(
    "metric_marks",
    _md,
    # Предмет события (сессия, матч, сборник…): повтор события с новым event_id метрики не двоит.
    Column("subject_key", String(200), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    _ts("at"),
    # Ретенция (ADR 0062): удаление старых отметок по времени.
    Index("ix_metric_marks_at", "at"),
)

wheel_spins = Table(
    "wheel_spins",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("day", Date, nullable=False),
    Column("sector_key", String(64), nullable=False),
    Column("prize_kind", String(8), nullable=False),
    Column("prize_amount", BigInteger, nullable=False),
    Column("prize_multiplier", Numeric(8, 3), nullable=True),
    Column("prize_matches", Integer, nullable=True),
    Column("prize_item_key", String(64), nullable=True),
    Column("config_version", Integer, ForeignKey("config_versions.version"), nullable=False),
    _ts("spun_at"),
    PrimaryKeyConstraint("user_id", "day"),
    CheckConstraint("prize_kind IN ('xp','verses','boost','item','none')", name="prize_kind_known"),
    CheckConstraint("prize_amount >= 0", name="prize_amount_non_negative"),
    CheckConstraint(
        "prize_kind <> 'boost' OR (prize_multiplier > 1 AND prize_matches >= 1)",
        name="boost_complete",
    ),
)

boosts = Table(
    "boosts",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("multiplier", Numeric(8, 3), nullable=False),
    Column("matches_left", Integer, nullable=False),
    Column("source", String(32), nullable=False),
    Column("source_ref", String(200), nullable=False),
    _ts("queued_at"),
    CheckConstraint("multiplier > 1", name="multiplier_above_one"),
    CheckConstraint("matches_left >= 0", name="matches_left_non_negative"),
    # Один источник - один буст: повтор выдачи не создаёт второй.
    UniqueConstraint("user_id", "source_ref", name="uq_boosts_user_source_ref"),
    Index(
        "ix_boosts_user_live",
        "user_id",
        "queued_at",
        "id",
        postgresql_where=text("matches_left > 0"),
    ),
)

boost_spends = Table(
    "boost_spends",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    Column("boost_id", Uuid(as_uuid=True), ForeignKey("boosts.id"), nullable=False),
    Column("multiplier", Numeric(8, 3), nullable=False),
    _ts("at"),
    PrimaryKeyConstraint("user_id", "match_id"),
    CheckConstraint("multiplier > 1", name="multiplier_above_one"),
)

grants_log = Table(
    "grants_log",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("source", String(32), nullable=False),
    Column("source_ref", String(200), nullable=False),
    Column("item_key", String(64), nullable=False),
    # В 5a выдача - заглушка: запись остаётся 'pending', реальную выдачу сделает инвентарь 5b.
    Column("status", String(16), nullable=False, server_default=text("'pending'")),
    _ts("at"),
    UniqueConstraint("source", "source_ref", name="uq_grants_log_source_ref"),
    CheckConstraint("status IN ('pending','granted')", name="status_known"),
    Index("ix_grants_log_user", "user_id", "at"),
)

REWARD_TABLES = (
    quest_assignments,
    quest_progress,
    badges_unlocked,
    metric_counters,
    metric_marks,
    wheel_spins,
    boosts,
    boost_spends,
    grants_log,
)
