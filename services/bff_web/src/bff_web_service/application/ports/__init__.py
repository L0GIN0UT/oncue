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
    AppearanceSlotCard,
    BadgeCard,
    CatalogQuery,
    CollectionDetail,
    CollectionList,
    CollectionMode,
    DailyStatus,
    Domain,
    InventoryCard,
    Item,
    ItemShelves,
    LadderCard,
    LeaderboardCard,
    MatchResult,
    Me,
    Mode,
    MyAvatar,
    MyMatches,
    OpenRooms,
    PackCard,
    PersonalRecord,
    PlayerAppearanceCard,
    ProgressCard,
    PublicProgress,
    QuestsCard,
    QueuePoolCard,
    RatingCard,
    SessionResult,
    Shelf,
    ShelfItems,
    ShopCatalogCard,
    WalletCard,
    WeeklyTopCard,
    WheelCard,
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

    async def queue_pool(self, token: str) -> list[QueuePoolCard]:
        """Сколько игроков ищут матч по каждому ladder-у и формату (4b, ADR 0038)."""
        ...


class RatingClient(Protocol):
    """Публичный API rating (4b).

    ``ladders`` и анонимные ``leaderboard``/``weekly`` публичны и кэшируются на секунды;
    с токеном (``me``, ``around=me``) и ``my_ratings`` — личные, не кэшируются.
    """

    async def ladders(self) -> list[LadderCard]:
        """Опубликованные ladder-ы с действующим сезоном и званиями (кэшируется)."""
        ...

    async def my_ratings(self, token: str) -> list[RatingCard]:
        """Рейтинги пользователя по ladder-ам. Гость — ``UpstreamRejectedError(403)``."""
        ...

    async def leaderboard(
        self, ladder: str, *, token: str | None, cursor: str | None, around_me: bool
    ) -> LeaderboardCard:
        """Лидерборд ladder-а; без токена — общий и кэшируется.

        Нет ladder-а — ``UpstreamRejectedError(404, rating.ladder_not_found)``,
        ``around_me`` в калибровке — ``404 rating.not_ranked``.
        """
        ...

    async def weekly(self, ladder: str, *, token: str | None) -> WeeklyTopCard:
        """Топ недели ladder-а; без токена — общий и кэшируется."""
        ...


class ProgressionClient(Protocol):
    """Публичный API progression (5a): всё личное, не кэшируется.

    Гость — ``UpstreamRejectedError(403)``, невалидный токен — 401.
    """

    async def progress(self, token: str) -> ProgressCard:
        """Уровень, опыт, серии дейлика, буст (``/v1/me/progress``)."""
        ...

    async def wallet(self, token: str) -> WalletCard:
        """Версы (``/v1/me/wallet``)."""
        ...

    async def quests(self, token: str) -> QuestsCard:
        """Дневные и недельные задания (``/v1/me/quests``)."""
        ...

    async def badges(self, token: str) -> list[BadgeCard]:
        """Бейджи (``/v1/me/badges``)."""
        ...

    async def wheel(self, token: str) -> WheelCard:
        """Колесо: секторы и сегодняшний спин (``/v1/wheel``)."""
        ...

    async def player(self, token: str, user_id: UUID) -> PublicProgress:
        """Публичный прогресс игрока (``/v1/users/{id}/progress``). Нет игрока — 404."""
        ...

    async def shop_items(self, token: str) -> ShopCatalogCard:
        """Витрина с состояниями и балансом (``/v1/shop/items``)."""
        ...

    async def shop_packs(self, token: str) -> list[PackCard]:
        """Паки (``/v1/shop/packs``); при ``billing.enabled=false`` — 404 ``billing_disabled``."""
        ...

    async def inventory(self, token: str) -> InventoryCard:
        """Инвентарь (``/v1/me/inventory``)."""
        ...

    async def my_appearance(self, token: str) -> list[AppearanceSlotCard]:
        """Слоты оформления субъекта (``/v1/me/appearance``)."""
        ...

    async def my_avatar(self, token: str) -> MyAvatar:
        """Своя аватарка: право, заявка, лимиты, действующий адрес (``/v1/me/avatar``)."""
        ...

    async def players_appearance(
        self, token: str, user_ids: list[UUID]
    ) -> list[PlayerAppearanceCard]:
        """Оформление игроков (``/v1/appearance?user_ids=``, до 12); неизвестные пропущены."""
        ...


class NotificationsClient(Protocol):
    """Публичный API notifications (5b): только личное, не кэшируется."""

    async def unread_count(self, token: str) -> int:
        """Число непрочитанных (``/v1/notifications/unread-count``)."""
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
