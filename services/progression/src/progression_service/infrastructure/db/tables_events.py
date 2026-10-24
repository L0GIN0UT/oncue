"""Схема БД сезонных событий (этап 5d, поток E1; ADR 0054-0056; миграция ``0008``).

``events`` - определение (тело JSON, хеш, ключ картинки карты в S3), ``event_runs`` - запуски со
статусом и неизменяемым снимком определения на публикации, ``event_find_progress`` - прогресс и
открытие находок игрока (по периоду метрики), ``event_completions`` - закрытие всех находок.
Инварианты - в схеме: одна открытая находка на (запуск, игрок, находка) и одно закрытие на игрока
не дают выдать награду дважды даже при гонке; внешние ключи ``RESTRICT``: запуск с прогрессом не
удалить.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from progression_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata


def _ts(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, DateTime(timezone=True), nullable=nullable)


events = Table(
    "events",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("key", String(64), nullable=False),
    Column("body", JSONB, nullable=False),
    Column("body_sha256", String(64), nullable=False),
    Column("map_key", String(64), nullable=True),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
    UniqueConstraint("key"),
    CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name="key_shape"),
    CheckConstraint("body_sha256 ~ '^[0-9a-f]{64}$'", name="sha_shape"),
)

event_runs = Table(
    "event_runs",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column(
        "event_id", Uuid(as_uuid=True), ForeignKey("events.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("seq", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    _ts("starts_at"),
    _ts("ends_at"),
    Column("snapshot", JSONB, nullable=True),
    Column("body_sha256", String(64), nullable=True),
    _ts("published_at", nullable=True),
    _ts("stopped_at", nullable=True),
    Column("created_by", Uuid(as_uuid=True), nullable=False),
    _ts("created_at"),
    UniqueConstraint("event_id", "seq"),
    CheckConstraint(
        "status IN ('draft','published','active','finished','stopped')", name="status_known"
    ),
    CheckConstraint("ends_at > starts_at", name="period_ordered"),
    CheckConstraint(
        "status = 'draft' OR (snapshot IS NOT NULL AND body_sha256 IS NOT NULL "
        "AND published_at IS NOT NULL)",
        name="published_has_snapshot",
    ),
    CheckConstraint("(status = 'stopped') = (stopped_at IS NOT NULL)", name="stopped_dated"),
    Index("ix_event_runs_status_starts", "status", "starts_at"),
)

event_find_progress = Table(
    "event_find_progress",
    _md,
    Column(
        "run_id",
        Uuid(as_uuid=True),
        ForeignKey("event_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("find_key", String(64), nullable=False),
    Column("period_key", String(16), nullable=False),
    Column("progress", Integer, nullable=False),
    _ts("unlocked_at", nullable=True),
    _ts("updated_at"),
    PrimaryKeyConstraint("run_id", "user_id", "find_key", "period_key"),
    CheckConstraint("progress >= 0", name="progress_non_negative"),
    CheckConstraint(
        "period_key = 'event' OR period_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'",
        name="period_key_shape",
    ),
    # Находка открывается один раз, даже если у неё строки за несколько суток.
    Index(
        "ux_event_find_progress_unlocked",
        "run_id",
        "user_id",
        "find_key",
        unique=True,
        postgresql_where=text("unlocked_at IS NOT NULL"),
    ),
    Index("ix_event_find_progress_user", "user_id", "run_id"),
)

event_completions = Table(
    "event_completions",
    _md,
    Column(
        "run_id",
        Uuid(as_uuid=True),
        ForeignKey("event_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    _ts("completed_at"),
    PrimaryKeyConstraint("run_id", "user_id"),
)
