"""События комментариев и жалоб (``contracts/events/social/*.v1.json``).

Поля совпадают со схемами один в один. Текст комментария и жалобы в события не входит.
Топик общий для сервиса, ``social.social.v1`` (ADR 0050): поэтому ``aggregate_type`` равен
``social``, а ключ партиции (``aggregate_id``) - идентификатор комментария или жалобы.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from gv_core.domain import DomainEvent

SOCIAL_AGGREGATE = "social"


@dataclass(frozen=True, kw_only=True)
class CommentPosted(DomainEvent):
    """Комментарий опубликован."""

    aggregate_type: ClassVar[str] = SOCIAL_AGGREGATE

    at: datetime
    author_id: UUID
    author_nickname: str
    collection_author_id: UUID
    collection_id: UUID
    collection_title: str
    comment_id: UUID
    notify_owner: bool


@dataclass(frozen=True, kw_only=True)
class CommentDeleted(DomainEvent):
    """Комментарий удалён."""

    aggregate_type: ClassVar[str] = SOCIAL_AGGREGATE

    at: datetime
    by_role: str
    collection_id: UUID
    comment_id: UUID
    deleted_by: UUID | None


@dataclass(frozen=True, kw_only=True)
class ReportFiled(DomainEvent):
    """Жалоба принята."""

    aggregate_type: ClassVar[str] = SOCIAL_AGGREGATE

    at: datetime
    author_id: UUID
    case_id: UUID | None
    context_match_id: UUID | None
    reason_code: str
    report_id: UUID
    target_id: UUID
    target_kind: str
    target_user_id: UUID
