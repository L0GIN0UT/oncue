"""Таблицы admin-API progression (этап 5a, поток P3): аудит и ключи идемпотентности.

``admin_actions`` — аудит ручных действий (конфиги, выдача, сторно); ``idempotency_keys`` — ключи
``(actor_id, Idempotency-Key)`` по ``gv_db.idempotency``. Частичный уникальный индекс
``uq_config_versions_draft`` держит в схеме «черновик один».
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, String, Table, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB

from gv_db.idempotency import idempotency_table
from progression_service.infrastructure.db.tables import TABLES, config_versions

_md = TABLES.metadata

admin_actions = Table(
    "admin_actions",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("actor_id", Uuid(as_uuid=True), nullable=False),
    Column("action", String(64), nullable=False),
    Column("target", String(200), nullable=False),
    Column("reason", Text, nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("at", DateTime(timezone=True), nullable=False),
    Index("ix_admin_actions_target_at", "target", "at"),
    Index("ix_admin_actions_actor_at", "actor_id", "at"),
)

idempotency_keys = idempotency_table(_md)

draft_unique = Index(
    "uq_config_versions_draft",
    config_versions.c.status,
    unique=True,
    postgresql_where=text("status = 'draft'"),
)
