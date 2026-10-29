"""Порты bff_web: клиенты сервисов-источников и кэш публичных блоков.

Клиенты различают два вида неудач:

* ``UpstreamUnavailableError`` — источник не ответил вовремя, упал (5xx), перегружен
  (429), открыт circuit breaker или ответ не соответствует контракту. Экран гасит блок
  (``status: unavailable``) и отвечает 200.
* ``UpstreamRejectedError`` — источник осознанно отказал (4xx Problem Details): нет прав,
  нет сессии. Что с этим делать, решает use-case (404 итога — 404 экрана).
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any, Protocol
from uuid import UUID

from bff_web_service.application.dto import (
    ActiveEvent,
    AppearanceCard,
    AvatarCard,
    BadgeCard,
    CatalogQuery,
    CollectionDetail,
    CollectionList,
    CollectionMode,
    CommentsCard,
    DailyResult,
    DailyStatus,
    Domain,
    EventArchiveCard,
    EventPageData,
    FriendCard,
    InventoryCard,
    Item,
    ItemShelves,
    LadderCard,
    LeaderboardCard,
    MatchResult,
    Me,
    Mode,
    MyMatches,
    OpenRooms,
    PackCard,
    PersonalRecord,
    PlayerAppearanceCard,
    PlayerRelation,
    ProgressCard,
    PublicProgress,
    QuestsCard,
    QueuePoolCard,
    RatingCard,
    SanctionsCard,
    SessionResult,
    SessionRewardsCard,
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

    async def daily_results(
        self, token: str, user_ids: Sequence[UUID], domain: Domain | None = None
    ) -> list[DailyResult]:
        """Завершённые дейлики перечисленных пользователей в текущем периоде (личный вызов).

        Не больше ``GV_GAME__DAILY_RESULTS_MAX_USERS`` игроков за вызов; кому можно показывать
        чужие очки, решает вызывающий (social). Не кэшируется.
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

    async def get_items(self, item_ids: Sequence[UUID]) -> list[Item]:
        """Карточки пачкой (``GET /v1/items?ids=``, не больше предела источника).

        Неизвестные и заблокированные единицы в ответе отсутствуют, порядок не гарантирован.
        """
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

    async def items_shelves(self, token: str, media_item_ids: Sequence[UUID]) -> list[ItemShelves]:
        """Полки пачки единиц (``GET /v1/items/shelves?ids=``): запись на каждую единицу."""
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

    async def session_rewards(self, token: str, session_id: UUID) -> SessionRewardsCard:
        """Награды за свою игровую сессию (``/v1/me/rewards``).

        Ещё не начислено (или сессия чужая) — ``settled = false`` с нулями, не ошибка.
        """
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

    async def my_appearance(self, token: str) -> AppearanceCard:
        """Оформление субъекта: слоты и своя аватарка (``/v1/me/appearance``)."""
        ...

    async def my_avatar(self, token: str) -> AvatarCard:
        """Своя аватарка: право, заявка, лимиты (``/v1/me/avatar``; адрес - в оформлении)."""
        ...

    async def players_appearance(
        self, token: str, user_ids: list[UUID]
    ) -> list[PlayerAppearanceCard]:
        """Оформление игроков (``/v1/appearance?user_ids=``, до 12); неизвестные пропущены."""
        ...

    async def active_event(self) -> ActiveEvent | None:
        """Активное событие (``/v1/events/active``, анонимно, кэш до 30 с); нет — ``None``."""
        ...

    async def event_page(self, token: str, event_key: str) -> EventPageData:
        """Страница события (``/v1/events/{key}``); нет события — 404."""
        ...

    async def event_progress(self, token: str, user_id: UUID, event_key: str) -> EventPageData:
        """Та же страница для баннера оболочки: короткий кэш на игрока."""
        ...

    async def my_events(self, token: str) -> list[EventArchiveCard]:
        """Архив событий игрока (``/v1/me/events``)."""
        ...


class NotificationsClient(Protocol):
    """Публичный API notifications (5b): только личное, не кэшируется."""

    async def unread_count(self, token: str) -> int:
        """Число непрочитанных (``/v1/notifications/unread-count``)."""
        ...


class SocialClient(Protocol):
    """Публичный API social (5c): друзья, комментарии, санкции; не кэшируется.

    Токен пользователя уходит как Bearer; у комментариев он необязателен (читают и анонимы).
    """

    async def relation(self, token: str, user_id: UUID) -> PlayerRelation:
        """Отношение зрителя к игроку (``/v1/players/{id}/relation``)."""
        ...

    async def relations(self, token: str, user_ids: list[UUID]) -> dict[UUID, PlayerRelation]:
        """Отношение зрителя к нескольким игрокам одним вызовом (``/v1/players/relations``)."""
        ...

    async def friends(self, token: str) -> list[FriendCard]:
        """Друзья владельца со статусом (``/v1/me/friends``)."""
        ...

    async def incoming_requests(self, token: str) -> int:
        """Число входящих заявок в друзья (``/v1/me/friend-requests``)."""
        ...

    async def comments(self, token: str | None, collection_id: UUID, *, limit: int) -> CommentsCard:
        """Первая страница комментариев сборника; ``can_*`` — для зрителя, если есть токен."""
        ...

    async def sanctions(self, token: str) -> SanctionsCard:
        """Действующие санкции владельца (``/v1/me/sanctions``)."""
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
