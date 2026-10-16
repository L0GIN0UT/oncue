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
    DailyStatus,
    Domain,
    Item,
    Me,
    Mode,
    PersonalRecord,
    SessionResult,
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
