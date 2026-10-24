"""Таблицы комментариев, жалоб и кейсов модерации social (5c, ADR 0051). Время - timestamptz.

* ``comments`` - плоские комментарии к сборникам; удаление мягкое (``deleted_*``), текст
  удалённого и обезличенного комментария очищается;
* ``collections_proj`` - проекция сборников из ``collections.*`` (автор, опубликован ли);
  заголовок хранится, если его пришлёт collections (в событиях его пока нет);
* ``reports`` - жалобы; ``UNIQUE(author_id, target_kind, target_id)`` - одна жалоба на объект;
* ``moderation_cases`` - кейсы; частичный UNIQUE даёт «один открытый кейс на объект»;
* ``case_reports`` - жалобы, вошедшие в кейс;
* ``match_participants_proj`` - участники матчей (для проверки «общий матч» при жалобе);
* ``behaviour_counters`` - счётчики порядочности (без автосанкций);
* ``comment_idempotency_keys`` - ключи идемпотентности публикации комментариев.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
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

from gv_db.idempotency import idempotency_table
from social_service.infrastructure.db.tables import TABLES

_md = TABLES.metadata
_TS = DateTime(timezone=True)

comments = Table(
    "comments",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("collection_id", Uuid(as_uuid=True), nullable=False),
    Column("author_id", Uuid(as_uuid=True), nullable=False),
    Column("body", String(500), nullable=False),
    Column("created_at", _TS, nullable=False),
    Column("edited_at", _TS, nullable=True),
    Column("deleted_at", _TS, nullable=True),
    Column("deleted_by", Uuid(as_uuid=True), nullable=True),
    Column("deleted_role", String(32), nullable=True),
    CheckConstraint("char_length(body) <= 500", name="body_max_length"),
    CheckConstraint(
        "deleted_role IS NULL OR deleted_role IN "
        "('author', 'collection_owner', 'moderator', 'account_deleted')",
        name="deleted_role_known",
    ),
    Index("ix_comments_collection_created", "collection_id", text("created_at DESC"), "id"),
    Index("ix_comments_author", "author_id"),
)

collections_proj = Table(
    "collections_proj",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("author_id", Uuid(as_uuid=True), nullable=True),
    Column("title", String(120), nullable=False, server_default=""),
    Column("status", String(16), nullable=False),
    Column("version", Integer, nullable=False),
    CheckConstraint("status IN ('published', 'unpublished')", name="status_known"),
)

reports = Table(
    "reports",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("author_id", Uuid(as_uuid=True), nullable=False),
    Column("target_kind", String(16), nullable=False),
    Column("target_id", Uuid(as_uuid=True), nullable=False),
    Column("target_user_id", Uuid(as_uuid=True), nullable=False),
    Column("context_match_id", Uuid(as_uuid=True), nullable=True),
    Column("reason_code", String(64), nullable=False),
    Column("text", String(300), nullable=True),
    Column("created_at", _TS, nullable=False),
    CheckConstraint(
        "target_kind IN ('player', 'collection', 'comment', 'avatar')", name="target_kind_known"
    ),
    UniqueConstraint("author_id", "target_kind", "target_id", name="uq_reports_author_target"),
    Index("ix_reports_target_created", "target_kind", "target_id", "created_at"),
    Index("ix_reports_author_created", "author_id", "created_at"),
)

moderation_cases = Table(
    "moderation_cases",
    _md,
    Column("id", Uuid(as_uuid=True), primary_key=True),
    Column("target_kind", String(16), nullable=False),
    Column("target_id", Uuid(as_uuid=True), nullable=False),
    Column("target_user_id", Uuid(as_uuid=True), nullable=False),
    Column("status", String(16), nullable=False),
    Column("assignee_id", Uuid(as_uuid=True), nullable=True),
    Column("decision", String(32), nullable=True),
    Column("opened_at", _TS, nullable=False),
    Column("resolved_by", Uuid(as_uuid=True), nullable=True),
    Column("resolved_at", _TS, nullable=True),
    Column("version", Integer, nullable=False, server_default="1"),
    CheckConstraint(
        "target_kind IN ('player', 'collection', 'comment', 'avatar')", name="target_kind_known"
    ),
    CheckConstraint("status IN ('open', 'in_work', 'escalated', 'resolved')", name="status_known"),
    # Один неразрешённый кейс на объект: параллельные жалобы не открывают второй.
    Index(
        "uq_moderation_cases_open_target",
        "target_kind",
        "target_id",
        unique=True,
        postgresql_where=text("status <> 'resolved'"),
    ),
    Index("ix_moderation_cases_queue", "status", "opened_at", "id"),
    Index("ix_moderation_cases_target_user", "target_user_id"),
)

case_reports = Table(
    "case_reports",
    _md,
    Column("case_id", Uuid(as_uuid=True), ForeignKey("moderation_cases.id"), nullable=False),
    Column("report_id", Uuid(as_uuid=True), ForeignKey("reports.id"), nullable=False),
    PrimaryKeyConstraint("case_id", "report_id"),
    Index("ix_case_reports_report", "report_id"),
)

match_participants_proj = Table(
    "match_participants_proj",
    _md,
    Column("match_id", Uuid(as_uuid=True), nullable=False),
    Column("user_id", Uuid(as_uuid=True), nullable=False),
    Column("solo", Boolean, nullable=False),
    Column("finished_at", _TS, nullable=False),
    PrimaryKeyConstraint("match_id", "user_id"),
    Index("ix_match_participants_user", "user_id"),
)

behaviour_counters = Table(
    "behaviour_counters",
    _md,
    Column("user_id", Uuid(as_uuid=True), primary_key=True),
    Column("forfeits", Integer, nullable=False, server_default="0"),
    Column("leaves", Integer, nullable=False, server_default="0"),
    Column("tech_losses", Integer, nullable=False, server_default="0"),
    Column("reports_received", Integer, nullable=False, server_default="0"),
    Column("praises", Integer, nullable=False, server_default="0"),
    Column("matches_total", Integer, nullable=False, server_default="0"),
)

COMMENT_IDEMPOTENCY = idempotency_table(_md, "comment_idempotency_keys")
