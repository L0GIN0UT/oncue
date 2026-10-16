"""Общее для use-cases: субъекты, проверка права автора, карточка сборника, курсоры."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from collections_service.application.ports import CollectionRow, CollectionsUnitOfWork, Json
from collections_service.application.views import Presenter
from collections_service.domain.access import Viewer, custom_visible, items_visible
from collections_service.domain.errors import AuthorRestrictedError
from gv_core.errors import InvalidInputError
from gv_core.pagination import decode_cursor, encode_cursor


@dataclass(frozen=True, slots=True)
class Actor:
    """Пользователь, выполняющий мутацию (гость сюда не попадает: ``require_user``).

    Attributes:
        user_id: Идентификатор пользователя.
        sanctioned: Санкция ``CUSTOM_CONTENT`` из claim токена (D8).
        is_admin: Есть ``admin.access`` и ``collections.view`` (видит скрытое).
        idempotency_key: ``Idempotency-Key`` запроса.
    """

    user_id: UUID
    idempotency_key: str
    sanctioned: bool = False
    is_admin: bool = False

    @property
    def viewer(self) -> Viewer:
        """Субъект чтения."""
        return Viewer(user_id=self.user_id, is_admin=self.is_admin)


@dataclass(frozen=True, slots=True)
class AdminContext:
    """Кто и зачем выполняет admin-мутацию."""

    actor_id: UUID
    reason: str
    idempotency_key: str


async def ensure_can_author(uow: CollectionsUnitOfWork, actor: Actor) -> None:
    """Автор может создавать, править и публиковать сборники.

    Raises:
        AuthorRestrictedError: санкция ``CUSTOM_CONTENT`` или ограничение модератора.
    """
    if actor.sanctioned or await uow.restrictions.get(actor.user_id) is not None:
        raise AuthorRestrictedError()


async def detail_json(
    uow: CollectionsUnitOfWork, presenter: Presenter, row: CollectionRow, viewer: Viewer
) -> Json:
    """``CollectionDetail`` для субъекта: ``me`` и состав по D6 (проверка на сервере)."""
    me: Json | None = None
    completed = False
    if viewer.user_id is not None:
        liked, favorite = await uow.reactions.state(viewer.user_id, row.collection_id)
        completed = await uow.stats.completed(
            viewer.user_id, row.collection_id, row.content_version
        )
        me = {
            "liked": liked,
            "favorite": favorite,
            "completed": completed,
            "is_author": viewer.user_id == row.author_id,
        }
    items: list[Json] | None = None
    if items_visible(row.author_id, viewer, completed=completed):
        rows = await uow.reads.items(row.collection_id)
        items = presenter.items(rows, with_custom=custom_visible(row.author_id, viewer))
    return presenter.detail(row, items=items, me=me)


# ---------------------------------------------------------------- курсоры
def cursor_of(scope: str, value: Any, last_id: UUID) -> str:
    """Курсор ``(scope, значение сортировки, id)``; ``datetime`` — в ISO."""
    raw = value.isoformat() if isinstance(value, datetime) else value
    return encode_cursor({"s": scope, "v": raw, "id": str(last_id)})


def _cursor_value(raw: Any, kind: type) -> Any:
    if kind is datetime:
        return datetime.fromisoformat(str(raw))
    if kind is float:
        return float(raw)
    return int(raw)


def parse_cursor(cursor: str | None, scope: str, *, kind: type) -> tuple[Any, UUID] | None:
    """Keyset из курсора; курсор другой сортировки или битый — 422 ``pagination.bad_cursor``."""
    if cursor is None:
        return None
    data = decode_cursor(cursor)
    if data.get("s") != scope:
        raise InvalidInputError("курсор другой выборки", code="pagination.bad_cursor")
    try:
        return _cursor_value(data["v"], kind), UUID(str(data["id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from exc
