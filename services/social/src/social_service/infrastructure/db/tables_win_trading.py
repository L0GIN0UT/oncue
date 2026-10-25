"""Таблицы детектора win-trading social (ADR 0068). Время - timestamptz.

* ``win_trading_matches`` - факты рейтинговых дуэлей 1×1 (пара ``user_low < user_high``,
  победитель, сдавшийся); один матч - одна строка, повтор события ничего не меняет;
* ``win_trading_signals`` - готовые сигналы для модератора (пары, серии сдач): пересчитываются
  при каждом новом факте по окнам конфига, читаются без пересборки.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, Table, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB

from social_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata
_TS = DateTime(timezone=True)

win_trading_matches = Table(
    "win_trading_matches",
    _md,
    Column("match_id", Uuid(as_uuid=True), primary_key=True),
    Column("user_low", Uuid(as_uuid=True), nullable=False),
    Column("user_high", Uuid(as_uuid=True), nullable=False),
    Column("winner_id", Uuid(as_uuid=True), nullable=True),
    Column("forfeiter_id", Uuid(as_uuid=True), nullable=True),
    Column("finished_at", _TS, nullable=False),
    CheckConstraint("user_low < user_high", name="pair_ordered"),
    Index("ix_win_trading_matches_pair", "user_low", "user_high", "finished_at"),
    Index("ix_win_trading_matches_high", "user_high", "finished_at"),
    Index(
        "ix_win_trading_matches_forfeiter",
        "forfeiter_id",
        "finished_at",
        postgresql_where=text("forfeiter_id IS NOT NULL"),
    ),
)

win_trading_signals = Table(
    "win_trading_signals",
    _md,
    Column("signal_key", String(120), primary_key=True),
    Column("kind", String(24), nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("other_user_id", Uuid(as_uuid=True), nullable=True),
    Column("match_count", Integer, nullable=False),
    Column("streak", Integer, nullable=False),
    Column("severity", String(8), nullable=False),
    Column("window_s", Integer, nullable=False),
    Column("first_at", _TS, nullable=False),
    Column("last_at", _TS, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("updated_at", _TS, nullable=False),
    CheckConstraint(
        "kind IN ('pair_repeat', 'forfeit_series', 'forfeit_window')", name="kind_known"
    ),
    CheckConstraint("severity IN ('medium', 'high')", name="severity_known"),
    Index("ix_win_trading_signals_queue", "last_at", "signal_key"),
    Index("ix_win_trading_signals_user", "user_id"),
    Index("ix_win_trading_signals_other", "other_user_id"),
)
