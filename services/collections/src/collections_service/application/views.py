"""JSON ответов API (форма схем ``api/schemas.py``): use-cases сохраняют их для
``Idempotency-Key`` как есть, слой API только валидирует модели.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from collections_service.application.ports import CollectionRow, ItemRow, Json, Restriction
from collections_service.domain.collection import Collection
from collections_service.domain.difficulty import DifficultyModel
from collections_service.domain.model import Draft, Kind, Status
from collections_service.domain.policy import ACTIVE
from gv_text.ugc import normalize_ugc

UNKNOWN_TITLE = "—"
"""Название единицы, которой (ещё) нет в проекции каталога."""


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _status(status: Status) -> str:
    # Удалённый сборник наружу не отдаётся; на всякий случай — как черновик.
    return str(status) if status is not Status.DELETED else str(Status.DRAFT)


def draft_json(collection: Collection, draft: Draft | None = None) -> Json:
    """``DraftOut``: черновик или тело опубликованной версии (``revision = 0``)."""
    view = draft or collection.draft_view()
    s = collection.state
    return {
        "collection_id": str(collection.id),
        "domain": str(s.domain),
        "status": _status(s.status),
        "revision": view.revision,
        "base_version": view.base_version,
        "client_label": view.client_label,
        "updated_at": view.updated_at.isoformat(),
        "body": view.body.to_json(),
    }


def author_json(kind: str, author_id: UUID | None, nickname: str | None) -> Json:
    """``AuthorOut``: пользователь, «Guessverse» (кураторский) или удалённый автор."""
    if kind == Kind.CURATED:
        return {"kind": "curator", "user_id": None, "nickname": None}
    if author_id is None:
        return {"kind": "deleted", "user_id": None, "nickname": None}
    return {"kind": "user", "user_id": str(author_id), "nickname": nickname}


class Presenter:
    """Карточки, страницы и строки админки по ``CollectionRow``.

    Args:
        difficulty: Корзины сложности (пороги из настроек применяются при чтении).
    """

    def __init__(self, difficulty: DifficultyModel) -> None:
        self._difficulty = difficulty

    def bucket(self, row: CollectionRow) -> str | None:
        """Корзина сложности строки."""
        bucket = self._difficulty.bucket(row.difficulty_score)
        return str(bucket) if bucket is not None else None

    def card(self, row: CollectionRow) -> Json:
        """``CollectionCard`` (каталог, избранное)."""
        return {
            "collection_id": str(row.collection_id),
            "domain": row.domain,
            "kind": row.kind,
            "title": row.title,
            "cover": {"preset": row.cover} if row.cover else None,
            "tags": list(row.tags),
            "author": author_json(row.kind, row.author_id, row.nickname),
            "default_mode_key": row.default_mode_key or "",
            "items_count": row.items_count,
            "likes_count": row.likes_count,
            "plays_count": row.plays_count,
            "difficulty": self.bucket(row),
            "featured": row.featured,
            "published_at": _iso(row.first_published_at or row.published_at),
        }

    @staticmethod
    def items(rows: Sequence[ItemRow], *, with_custom: bool) -> list[Json]:
        """``CollectionItemOut`` (свои эмодзи — только автору и админу)."""
        return [
            {
                "position": r.position,
                "media_item_id": str(r.media_item_id),
                "title": r.title or UNKNOWN_TITLE,
                "year": r.year,
                "available": r.status == ACTIVE,
                "custom": {"emoji": r.emoji} if with_custom and r.emoji else None,
            }
            for r in rows
        ]

    def detail(
        self,
        row: CollectionRow,
        *,
        items: list[Json] | None,
        me: Json | None,
    ) -> Json:
        """``CollectionDetail``: ``items: null``, если состав субъекту не открыт (D6)."""
        return {
            **self.card(row),
            "description": row.description,
            "visibility": row.visibility,
            "status": str(Status.HIDDEN if row.status is Status.HIDDEN else Status.PUBLISHED),
            "version": row.content_version,
            "items_visible": items is not None,
            "items": items,
            "me": me,
        }

    @staticmethod
    def mine(row: CollectionRow) -> Json:
        """``MyCollectionOut``."""
        published = row.content_version > 0
        return {
            "collection_id": str(row.collection_id),
            "domain": row.domain,
            "kind": row.kind,
            "status": _status(row.status),
            "title": normalize_ugc(row.title) or UNKNOWN_TITLE,
            "visibility": row.visibility,
            "has_draft": row.draft_revision is not None,
            "draft_revision": row.draft_revision,
            "version": row.content_version if published else None,
            "items_count": row.items_count if published else (row.draft_items or 0),
            "likes_count": row.likes_count,
            "hidden_by_moderator": row.status is Status.HIDDEN,
            "updated_at": row.updated_at.isoformat(),
            "published_at": _iso(row.first_published_at),
        }

    def admin(self, row: CollectionRow, *, restricted: bool) -> Json:
        """``AdminCollectionOut``."""
        published = row.content_version > 0
        hidden = (
            {
                "reason": row.hidden_reason or "",
                "actor_id": str(row.hidden_by) if row.hidden_by else None,
                "at": _iso(row.hidden_at),
            }
            if row.status is Status.HIDDEN and row.hidden_at is not None
            else None
        )
        return {
            "collection_id": str(row.collection_id),
            "domain": row.domain,
            "kind": row.kind,
            "status": _status(row.status),
            "visibility": row.visibility,
            "title": normalize_ugc(row.title) or UNKNOWN_TITLE,
            "author": author_json(row.kind, row.author_id, row.nickname),
            "author_restricted": restricted,
            "featured": row.featured,
            "featured_rank": row.featured_rank,
            "version": row.content_version if published else None,
            "items_count": row.items_count if published else (row.draft_items or 0),
            "likes_count": row.likes_count,
            "plays_count": row.plays_count,
            "difficulty": self.bucket(row),
            "hidden": hidden,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "published_at": _iso(row.first_published_at),
        }

    def admin_detail(self, row: CollectionRow, *, restricted: bool, items: list[Json]) -> Json:
        """``AdminCollectionDetail``: тексты и состав с эмодзи."""
        return {
            **self.admin(row, restricted=restricted),
            "description": row.description,
            "tags": list(row.tags),
            "cover": {"preset": row.cover} if row.cover else None,
            "default_mode_key": row.default_mode_key,
            "draft_revision": row.draft_revision,
            "items": items,
        }


def restriction_json(
    user_id: UUID, restriction: Restriction | None, *, lifted: Restriction | None = None
) -> Json:
    """``RestrictionOut``: действующее ограничение или снятое (``lifted`` — кто и почему)."""
    shown = restriction or lifted
    return {
        "user_id": str(user_id),
        "restricted": restriction is not None,
        "reason": shown.reason if shown else None,
        "actor_id": str(shown.actor_id) if shown and shown.actor_id else None,
        "at": shown.at.isoformat() if shown else None,
    }
