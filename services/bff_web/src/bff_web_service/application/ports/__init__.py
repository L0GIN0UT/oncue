"""Порты bff_web: клиенты сервисов-источников и кэш публичных блоков.

Клиенты различают два вида неудач:

* ``UpstreamUnavailableError`` — источник не ответил вовремя, упал (5xx), перегружен
  (429), открыт circuit breaker или ответ не соответствует контракту. Экран гасит блок
  (``status: unavailable``) и отвечает 200.
* ``UpstreamRejectedError`` — источник осознанно отказал (4xx Problem Details): нет прав,
  нет сессии. Что с этим делать, решает use-case (404 итога — 404 экрана).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Protocol
from uuid import UUID

from bff_web_service.application.dto import (
    CatalogQuery,
    CollectionDetail,
    CollectionList,
    CollectionMode,
    DailyStatus,
    Domain,
    Item,
    ItemShelves,
    MatchResult,
    Me,
    Mode,
    MyMatches,
    OpenRooms,
    PersonalRecord,
    SessionResult,
    Shelf,
    ShelfItems,
)

TRACE_HEADERS: ContextVar[dict[str, str] | None] = ContextVar("bff_trace_headers", default=None)
"""Заголовки трассировки входящего запроса (``traceparent``, ``tracestate``).

Кладёт API на входе, читают HTTP-клиенты: так контекст трассы доходит до источников, даже
когда в процессе bff трейсинг выключен."""


class UpstreamUnavailableError(Exception):
    """Источник недоступен (таймаут, 5xx, 429, circuit breaker, ответ вне контракта)."""

    def __init__(self, service: str, reason: str) -> None:
        super().__init__(f"{service}: {reason}")
        self.service = service
        self.reason = reason


class UpstreamRejectedError(Exception):
    """Источник отказал (4xx Problem Details).

    Attributes:
        status: HTTP-статус ответа источника.
        code: Машинный код ошибки источника (``game.session_not_found``).
    """

    def __init__(
        self, service: str, status: int, code: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(f"{service}: {status} {code}")
        self.service = service
        self.status = status
        self.code = code
        self.details = details or {}


class GameClient(Protocol):
    """Публичный API game (``/api/game/v1`` на gateway, внутри — ``/v1``)."""

    async def list_modes(self) -> list[Mode]:
        """Опубликованные режимы всех доменов (публичные, кэшируются)."""
        ...

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        """Плановые режимы периода (дейлики и недельная сетка) субъекта токена.

        Личные, не кэшируются.
        """
        ...

    async def records(self, token: str) -> list[PersonalRecord]:
        """Личные рекорды субъекта токена (гостю — пустой список; не кэшируются)."""
        ...

    async def session_result(self, token: str, session_id: UUID) -> SessionResult:
        """Итог своей завершённой сессии."""
        ...

    async def collection_modes(self, collection_id: UUID) -> list[CollectionMode]:
        """Режимы сборника с играбельностью (публичные, не зависят от субъекта; кэшируются).

        Неопубликованный, скрытый или ещё не спроецированный сборник —
        ``UpstreamRejectedError(404)``.
        """
        ...

    async def online_modes(self) -> list[Mode]:
        """Онлайн-режимы для создания комнаты (``/v1/modes?session=online``; кэшируются)."""
        ...


class CatalogClient(Protocol):
    """Публичный API catalog."""

    async def get_item(self, item_id: UUID) -> Item:
        """Публичная карточка единицы (кэшируется). 404 — ``UpstreamRejectedError``."""
        ...


class IdentityClient(Protocol):
    """Публичный API identity."""

    async def me(self, token: str) -> Me:
        """Профиль субъекта токена. Гость — ``UpstreamRejectedError(403)``."""
        ...


class CollectionsClient(Protocol):
    """Публичный API collections."""

    async def catalog(self, query: CatalogQuery) -> CollectionList:
        """Каталог сборников (без токена: каталог одинаков для всех).

        Без ``q`` и курсора — кэшируется.
        """
        ...

    async def detail(self, token: str | None, collection_id: UUID) -> CollectionDetail:
        """Сборник глазами субъекта токена: ``items`` и ``me`` зависят от него (D6).

        Нет, скрыт для чужих или удалён — ``UpstreamRejectedError(404)``.
        """
        ...


class LibraryClient(Protocol):
    """Публичный API library (всё личное, не кэшируется)."""

    async def shelves(self, token: str) -> list[Shelf]:
        """Полки пользователя (системные первыми). Гость — ``UpstreamRejectedError(403)``."""
        ...

    async def shelf_items(
        self, token: str, shelf_ref: str, *, cursor: str | None, limit: int
    ) -> ShelfItems:
        """Полка и страница её единиц. Чужая или нет — ``UpstreamRejectedError(404)``."""
        ...

    async def item_shelves(self, token: str, media_item_id: UUID) -> ItemShelves:
        """На каких полках пользователя лежит единица (флаг «в медиатеке»)."""
        ...


class MatchClient(Protocol):
    """Публичный API match (всё только пользователю, D13; не кэшируется).

    Гость — ``UpstreamRejectedError(403, auth.guest_forbidden)``, аноним — 401.
    """

    async def open_rooms(self, token: str, *, limit: int) -> OpenRooms:
        """Публичные комнаты в фазе ``lobby`` со свободными местами, новые первыми."""
        ...

    async def my_matches(self, token: str, *, limit: int) -> MyMatches:
        """Сыгранные матчи субъекта токена, новые первыми."""
        ...

    async def match(self, token: str, match_id: UUID) -> MatchResult:
        """Итог завершённого матча. Нет или ещё идёт — ``UpstreamRejectedError(404)``."""
        ...


class PageCache(Protocol):
    """Кэш публичных (не зависящих от пользователя) ответов источников.

    Ошибки хранилища не должны ронять страницу: реализация гасит их и ведёт себя как
    промах кэша.
    """

    async def get(self, key: str) -> bytes | None:
        """Значение или ``None``."""
        ...

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        """Сохранить на ``ttl_s`` секунд."""
        ...
