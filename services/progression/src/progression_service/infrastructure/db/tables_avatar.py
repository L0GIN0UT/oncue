"""Таблицы своей аватарки (этап 5b, поток S3; ADR 0048).

``avatar_requests`` - заявки по ассетам media-pipeline (``asset_id`` уникален; ``version`` -
последняя применённая версия ``AssetModerated`` по ассету). ``user_avatars`` - действующая
одобренная аватарка игрока (одна на игрока), ``public_key`` - ``u/<hex>.webp`` в публичном
префиксе gateway ``/ugc/``.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    text,
)

from progression_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata

avatar_requests = Table(
    "avatar_requests",
    _md,
    Column("id", Uuid, primary_key=True),
    Column("user_id", Uuid, nullable=False),
    Column("asset_id", Uuid, nullable=False),
    Column("status", String(16), nullable=False),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("reason_code", String(64), nullable=True),
    Column("version", Integer, nullable=False, server_default=text("0")),
    UniqueConstraint("asset_id"),
    CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
    CheckConstraint("version >= 0", name="version_non_negative"),
    CheckConstraint("status <> 'pending' OR reason_code IS NULL", name="pending_has_no_reason"),
    Index("ix_avatar_requests_user_submitted", "user_id", "submitted_at"),
    # Инвариант схемы: у игрока не больше одной заявки в ожидании решения.
    Index(
        "ux_avatar_requests_one_pending",
        "user_id",
        unique=True,
        postgresql_where=text("status = 'pending'"),
    ),
)

user_avatars = Table(
    "user_avatars",
    _md,
    Column("user_id", Uuid, primary_key=True),
    Column("asset_id", Uuid, nullable=False),
    Column("public_key", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("approved_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("public_key ~ '^u/[a-f0-9]{32}[.]webp$'", name="public_key_shape"),
    CheckConstraint("version >= 1", name="version_positive"),
)
