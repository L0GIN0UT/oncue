"""Кто что видит (D6, D7, §12 п. 1): решения на сервере, тест на каждого субъекта.

* удалённый и ни разу не опубликованный сборник — никому (404; автор правит черновик
  через ``/draft``);
* скрытый — только автору и админу;
* состав (``items``) — автору, админу и пользователю, завершившему текущую версию;
  гость и аноним состава не видят никогда, свои эмодзи — только автор и админ.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from collections_service.domain.model import Status

ADMIN_PERMS = ("admin.access", "collections.view")


@dataclass(frozen=True, slots=True)
class Viewer:
    """Субъект запроса чтения.

    Attributes:
        user_id: Пользователь; ``None`` — аноним или гость.
        is_admin: Есть ``admin.access`` и ``collections.view``.
    """

    user_id: UUID | None = None
    is_admin: bool = False

    @classmethod
    def anonymous(cls) -> Viewer:
        """Аноним или гость."""
        return cls()


def can_view(status: Status, author_id: UUID | None, viewer: Viewer) -> bool:
    """Карточка сборника видна субъекту."""
    if status is Status.PUBLISHED:
        return True
    if status is Status.HIDDEN:
        return viewer.is_admin or (viewer.user_id is not None and viewer.user_id == author_id)
    return False


def items_visible(author_id: UUID | None, viewer: Viewer, *, completed: bool) -> bool:
    """Состав открыт субъекту (D6)."""
    if viewer.is_admin:
        return True
    if viewer.user_id is None:
        return False
    return viewer.user_id == author_id or completed


def custom_visible(author_id: UUID | None, viewer: Viewer) -> bool:
    """Свои эмодзи единиц видны субъекту (автор и админ)."""
    return viewer.is_admin or (viewer.user_id is not None and viewer.user_id == author_id)
