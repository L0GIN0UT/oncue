"""Комментарии, жалобы и счётчики порядочности: модели и чистые правила (5c, ADR 0051).

Пределы (окно правки, порог кейса, лимиты) приходят из конфига ``SocialConfig``; здесь только
вычисления без IO.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

# Коды ошибок (контракт ``contracts/openapi/social.json``).
COLLECTION_NOT_FOUND = "social.collection_not_found"
COMMENT_NOT_FOUND = "social.comment_not_found"
COMMENT_REJECTED = "social.comment_rejected"
COMMENT_EDIT_EXPIRED = "social.comment_edit_expired"
COMMENTS_RATE_LIMIT = "social.comments_rate_limit"
CHAT_MUTED = "social.chat_muted"
CONFIG_UNAVAILABLE = "social.config_unavailable"
REPORT_NO_SHARED_MATCH = "social.report_no_shared_match"
REPORT_SELF = "social.report_self"
REPORT_REASON_UNKNOWN = "social.report_reason_unknown"
REPORT_TEXT_TOO_LONG = "social.report_text_too_long"
REPORT_TARGET_NOT_FOUND = "social.report_target_not_found"
REPORTS_RATE_LIMIT = "social.reports_rate_limit"

DELETE_ANY_PERM = "social.comments.delete_any"
DEFAULT_LANG = "ru"

ROLE_AUTHOR = "author"
ROLE_COLLECTION_OWNER = "collection_owner"
ROLE_MODERATOR = "moderator"
ROLE_ACCOUNT_DELETED = "account_deleted"

# Поле счётчика по исходу игрока в MatchFinished (completed/tech_win счётчик не меняют).
_COUNTER_BY_FINISH = {"forfeit": "forfeits", "left": "leaves", "tech_loss": "tech_losses"}


@dataclass(frozen=True, slots=True)
class Comment:
    """Комментарий к сборнику."""

    id: UUID
    collection_id: UUID
    author_id: UUID
    body: str
    created_at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_by: UUID | None = None
    deleted_role: str | None = None

    @property
    def is_deleted(self) -> bool:
        """Удалён ли комментарий."""
        return self.deleted_at is not None


@dataclass(frozen=True, slots=True)
class CollectionRef:
    """Сборник в проекции social: автор и признак «опубликован»."""

    id: UUID
    author_id: UUID | None
    title: str
    published: bool
    version: int


def can_edit(comment: Comment, viewer_id: UUID, now: datetime, window_s: int) -> bool:
    """Автор вправе править комментарий, пока не вышло окно ``edit_window_s``."""
    return (
        not comment.is_deleted
        and comment.author_id == viewer_id
        and now - comment.created_at <= timedelta(seconds=window_s)
    )


def delete_role(
    comment: Comment, actor_id: UUID, perms: frozenset[str], collection_author_id: UUID | None
) -> str | None:
    """Роль, по которой актор вправе удалить комментарий, или ``None`` (нет права).

    Автор имеет приоритет над автором сборника и модератором: в событии фиксируется
    наиболее личная роль.
    """
    if comment.author_id == actor_id:
        return ROLE_AUTHOR
    if collection_author_id is not None and collection_author_id == actor_id:
        return ROLE_COLLECTION_OWNER
    if DELETE_ANY_PERM in perms:
        return ROLE_MODERATOR
    return None


def threshold_reached(recent_reports: int, threshold: int) -> bool:
    """Достаточно ли жалоб за сутки на объект, чтобы открыть кейс."""
    return recent_reports >= threshold


def counter_for_finish(finish: str) -> str | None:
    """Поле ``behaviour_counters`` для исхода игрока или ``None``."""
    return _COUNTER_BY_FINISH.get(finish)


def localized(titles: Mapping[str, str], lang: str = DEFAULT_LANG) -> str:
    """Название на языке ``lang``; нет — на русском, иначе первое по алфавиту ключа."""
    if lang in titles:
        return titles[lang]
    if DEFAULT_LANG in titles:
        return titles[DEFAULT_LANG]
    return titles[min(titles)] if titles else ""
