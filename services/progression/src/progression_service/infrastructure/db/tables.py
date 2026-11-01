"""Схема БД progression (этап 5a, поток P1; ADR 0042, 0043).

Журналы ``xp_ledger`` и ``currency_ledger`` — append-only (триггер в миграции ``0002`` запрещает
UPDATE, DELETE и TRUNCATE), ``UNIQUE(op_id)`` — идемпотентность по предмету операции. Баланс
(``balances``) и ``progress`` обновляются в той же транзакции под блокировкой строки, баланс не
бывает ниже нуля. Все суммы целые.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB

from gv_db import create_metadata

TABLES = create_metadata()
_md = TABLES.metadata

OP_ID = String(200)
LEDGER_TABLES = ("xp_ledger", "currency_ledger")
"""Append-only журналы: на них стоит триггер запрета UPDATE/DELETE/TRUNCATE."""


def _ts(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


config_versions = Table(
    "config_versions",
    _md,
    Column("version", Integer, primary_key=True, autoincrement=False),
    Column("status", String(16), nullable=False),
    Column("body", JSONB, nullable=False),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    _ts("published_at", nullable=True),
    CheckConstraint("version >= 1", name="version_positive"),
    CheckConstraint("status IN ('draft','published','archived')", name="status_known"),
    CheckConstraint("status <> 'published' OR published_at IS NOT NULL", name="published_dated"),
    # Одна действующая версия — в схеме.
    Index(
        "uq_config_versions_published",
        "status",
        unique=True,
        postgresql_where=text("status = 'published'"),
    ),
)

progress = Table(
    "progress",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("total_xp", BigInteger, nullable=False),
    Column("level", Integer, nullable=False),
    Column("config_version", Integer, ForeignKey("config_versions.version"), nullable=False),
    _ts("updated_at"),
    # Скрытие профиля: поле модели, ручка — в 5c (R11).
    Column("hidden", Boolean, nullable=False, server_default=text("false")),
    CheckConstraint("total_xp >= 0", name="total_xp_non_negative"),
    CheckConstraint("level >= 1", name="level_positive"),
)


def _ledger(name: str, *extra: Any) -> Table:
    """Журнал: общие колонки и инварианты; ``extra`` — колонки и ограничения таблицы."""
    return Table(
        name,
        _md,
        Column("id", Uuid(as_uuid=True), primary_key=True),
        Column("op_id", OP_ID, nullable=False),
        Column("user_id", Uuid(as_uuid=True), nullable=False),
        Column("direction", String(8), nullable=False),
        Column("amount", BigInteger, nullable=False),
        Column("reason", String(64), nullable=False),
        Column("source_ref", String(200), nullable=False),
        # op_id исходной проводки; UNIQUE — у проводки не больше одного сторно.
        Column("reversal_of", OP_ID, nullable=True),
        Column("actor_id", Uuid(as_uuid=True), nullable=True),
        Column("config_version", Integer, ForeignKey("config_versions.version"), nullable=False),
        _ts("at"),
        UniqueConstraint("op_id"),
        UniqueConstraint("reversal_of"),
        ForeignKeyConstraint(["reversal_of"], [f"{name}.op_id"]),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("direction IN ('credit','debit')", name="direction_known"),
        CheckConstraint("reversal_of IS NULL OR reversal_of <> op_id", name="reversal_not_self"),
        # Свежие проводки игрока: обратный проход по индексу.
        Index(f"ix_{name}_user_at", "user_id", "at", "id"),
        # Награды за игровую сессию игрока (``GET /v1/me/rewards``): точечное чтение по предмету.
        Index(f"ix_{name}_user_source_ref", "user_id", "source_ref"),
        *extra,
    )


xp_ledger = _ledger(
    "xp_ledger",
    # До буста и множитель — для аудита; amount уже с бустом.
    Column("base_amount", BigInteger, nullable=False),
    Column("boost_multiplier", Numeric(8, 3), nullable=False),
    CheckConstraint("base_amount > 0", name="base_amount_positive"),
    CheckConstraint("boost_multiplier > 0", name="boost_positive"),
)

currency_ledger = _ledger(
    "currency_ledger",
    Column("bucket", String(8), nullable=False),
    CheckConstraint("bucket IN ('earned','paid')", name="bucket_known"),
)

balances = Table(
    "balances",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("bucket", String(8), nullable=False),
    Column("amount", BigInteger, nullable=False),
    _ts("updated_at"),
    PrimaryKeyConstraint("user_id", "bucket"),
    CheckConstraint("amount >= 0", name="amount_non_negative"),
    CheckConstraint("bucket IN ('earned','paid')", name="bucket_known"),
)

daily_counters = Table(
    "daily_counters",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("day", Date, nullable=False),
    Column("solo_xp", BigInteger, nullable=False, server_default=text("0")),
    Column("solo_verses", BigInteger, nullable=False, server_default=text("0")),
    Column("online_verses", BigInteger, nullable=False, server_default=text("0")),
    Column("first_win_awarded", Boolean, nullable=False, server_default=text("false")),
    # 0013: опыт суток с пользовательских сборников (потолок ``ugc_guard.ugc_daily_xp_cap``).
    Column("ugc_xp", BigInteger, nullable=False, server_default=text("0")),
    PrimaryKeyConstraint("user_id", "day"),
    CheckConstraint(
        "solo_xp >= 0 AND solo_verses >= 0 AND online_verses >= 0", name="counters_non_negative"
    ),
    CheckConstraint("ugc_xp >= 0", name="ugc_xp_non_negative"),
)

collection_authors = Table(
    "collection_authors",
    _md,
    Column("collection_id", Uuid(as_uuid=True), primary_key=True),
    Column("kind", String(8), nullable=False),
    Column("author_id", Uuid(as_uuid=True), nullable=True),
    _ts("updated_at"),
    CheckConstraint("kind IN ('ugc', 'curated')", name="kind_known"),
)
"""Проекция «сборник → автор» из ``collections.CollectionPublished`` (0013)."""

collection_play_marks = Table(
    "collection_play_marks",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("collection_id", Uuid(as_uuid=True), nullable=False),
    Column("rewarded_at", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("user_id", "collection_id"),
)
"""Когда игроку последний раз засчитали награду за сборник (окно повтора из конфига)."""

daily_streaks = Table(
    "daily_streaks",
    _md,
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("domain", String(16), nullable=False),
    Column("current", Integer, nullable=False),
    Column("best", Integer, nullable=False),
    Column("last_day", Date, nullable=True),
    PrimaryKeyConstraint("user_id", "domain"),
    CheckConstraint("domain IN ('music','film')", name="domain_known"),
    CheckConstraint("current >= 0 AND best >= current", name="streak_ordered"),
)

accrual_marks = Table(
    "accrual_marks",
    _md,
    Column("op_id", OP_ID, primary_key=True),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("reason", String(64), nullable=False),
    _ts("at"),
)
"""Нулевая отметка предмета: сумма после потолка равна 0, проводки нет, но предмет учтён.

Не append-only-журнал, но кодом не удаляется: повтор события не начисляет задним числом.
"""

reward_periods = Table(
    "reward_periods",
    _md,
    Column("period_key", String(200), nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    _ts("awarded_at"),
    PrimaryKeyConstraint("period_key", "user_id"),
)

reconcile_checkpoints = Table(
    "reconcile_checkpoints",
    _md,
    # Имя журнала (``xp_ledger`` / ``currency_ledger``): по строке на журнал.
    Column("ledger", String(32), primary_key=True),
    # Id последней проводки, покрытой сверкой (uuid7 упорядочен по времени создания).
    Column("last_id", Uuid(as_uuid=True), nullable=True),
    _ts("last_full_at", nullable=True),
    # Расхождения последнего полного прохода: держат алерт между инкрементальными проходами.
    Column("full_mismatches", Integer, nullable=False, server_default=text("0")),
    _ts("updated_at"),
    CheckConstraint("full_mismatches >= 0", name="full_mismatches_non_negative"),
)
"""Контрольные точки инкрементальной сверки (ADR 0062). Не журнал: точка только сдвигается."""
