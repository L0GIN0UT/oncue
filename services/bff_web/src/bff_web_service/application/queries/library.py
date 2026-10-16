"""Экраны медиатеки: полки и страница полки (план этапа 3b, §9; D11 — всё приватно).

Медиатека есть только у пользователя (D7): аноним получает 401, гость — 403
``auth.guest_forbidden``, library при этом не вызывается. Отказ library (чужая полка,
отозванная сессия) — ответ экрана; недоступность — блок ``unavailable``, страница 200.
"""

from __future__ import annotations

from bff_web_service.application.dto import (
    LibraryBlocks,
    LibraryPage,
    LibraryShelfBlocks,
    LibraryShelfPage,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import LibraryClient, UpstreamRejectedError
from bff_web_service.application.queries.common import (
    GUEST_FORBIDDEN,
    Limiter,
    block_of,
    screen_error,
)
from gv_core.errors import ForbiddenError, UnauthenticatedError


def _user_token(viewer: Viewer) -> str:
    """Токен пользователя или отказ экрана без вызова library.

    Непроверенный токен (JWKS недоступен) уходит в library: она проверит его сама.
    """
    if viewer.token is None:
        raise UnauthenticatedError("требуется вход")
    if viewer.kind is SubjectKind.GUEST:
        raise ForbiddenError("медиатека доступна только пользователям", code=GUEST_FORBIDDEN)
    return viewer.token


class GetLibraryPage:
    """Медиатека: полки пользователя (системные первыми — порядок library)."""

    def __init__(self, library: LibraryClient, *, concurrency: int) -> None:
        self._library, self._concurrency = library, concurrency

    async def __call__(self, viewer: Viewer) -> LibraryPage:
        """Собрать страницу.

        Raises:
            UnauthenticatedError: аноним или library не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость.
        """
        token = _user_token(viewer)
        shelves = await Limiter(self._concurrency).attempt(self._library.shelves(token))
        if isinstance(shelves, UpstreamRejectedError):
            raise screen_error(shelves, not_found="медиатека не найдена")
        return LibraryPage(blocks=LibraryBlocks(shelves=block_of(shelves, "library.shelves")))


class GetLibraryShelfPage:
    """Полка медиатеки: карточка полки и страница её единиц по курсору."""

    def __init__(self, library: LibraryClient, *, concurrency: int, items_limit: int) -> None:
        self._library, self._concurrency, self._limit = library, concurrency, items_limit

    async def __call__(
        self, viewer: Viewer, shelf_ref: str, *, cursor: str | None
    ) -> LibraryShelfPage:
        """Собрать страницу полки.

        Raises:
            UnauthenticatedError / ForbiddenError: как у ``GetLibraryPage``.
            NotFoundError: ``library.shelf_not_found`` — нет такой полки или она чужая.
            InvalidInputError: library отклонил курсор.
        """
        token = _user_token(viewer)
        page = await Limiter(self._concurrency).attempt(
            self._library.shelf_items(token, shelf_ref, cursor=cursor, limit=self._limit)
        )
        if isinstance(page, UpstreamRejectedError):
            raise screen_error(page, not_found="полка не найдена")
        return LibraryShelfPage(blocks=LibraryShelfBlocks(shelf=block_of(page, "library.shelf")))
