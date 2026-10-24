"""Сезонные события (5d, ADR 0054): тема оболочки, баннер и страница события.

Активное событие общее для всех, кэшируется клиентом (<= 30 с). progression недоступен -
деградация: тема по умолчанию, баннера нет. Страница события - только пользователь.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from bff_web_service.application.dto import (
    ActiveEvent,
    EventBannerCard,
    EventPageData,
    EventPageResult,
    SubjectKind,
    ThemeCard,
    Viewer,
)
from bff_web_service.application.ports import (
    ProgressionClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import GUEST_FORBIDDEN, Limiter, screen_error
from bff_web_service.application.queries.rating import user_token
from gv_core.errors import DependencyUnavailableError, ForbiddenError, UnauthenticatedError

log = structlog.get_logger(__name__)

_THEME_KEY = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class EventSnapshot:
    """Что оболочке и главной нужно от события: ключ темы и баннер."""

    theme_key: str
    banner: EventBannerCard | None


class EventsContext:
    """Активное событие для шапки и главной (с деградацией)."""

    def __init__(
        self, progression: ProgressionClient, *, default_theme_key: str, concurrency: int
    ) -> None:
        self._progression, self._default = progression, default_theme_key
        self._concurrency = concurrency

    async def active(self) -> ActiveEvent | None:
        """Активное событие; любой сбой источника - ``None`` (деградация)."""
        try:
            return await self._progression.active_event()
        except (UpstreamUnavailableError, UpstreamRejectedError) as exc:
            log.warning("bff.event_unavailable", reason=type(exc).__name__)
            return None

    def resolve_theme(self, key: str) -> str:
        """Ключ темы запуска; пустой или неизвестного вида - ``default_theme_key``."""
        return key if _THEME_KEY.fullmatch(key) else self._default

    def theme_key(self, event: ActiveEvent | None) -> str:
        """Ключ темы активного события (нет события - по умолчанию)."""
        return self.resolve_theme(event.theme_key) if event is not None else self._default

    async def snapshot(self, viewer: Viewer) -> EventSnapshot:
        """Тема и баннер; ``found``/``total`` - только вошедшему игроку."""
        event = await self.active()
        theme = self.theme_key(event)
        if event is None:
            return EventSnapshot(theme, None)
        found = total = None
        token = user_token(viewer)
        if token is not None:
            call = (
                self._progression.event_page(token, event.key)
                if viewer.user_id is None
                else self._progression.event_progress(token, viewer.user_id, event.key)
            )
            page = await Limiter(self._concurrency).attempt(call)
            if isinstance(page, EventPageData):
                found, total = page.found, page.total
        return EventSnapshot(
            theme, EventBannerCard(event.key, event.title, event.ends_at, found, total)
        )


class GetEventPage:
    """Страница события: данные progression и тема запуска. Только пользователь."""

    def __init__(self, progression: ProgressionClient, events: EventsContext) -> None:
        self._progression, self._events = progression, events

    async def __call__(self, viewer: Viewer, event_key: str) -> EventPageResult:
        """Собрать страницу.

        Raises:
            UnauthenticatedError: аноним или progression не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` - гость.
            NotFoundError: нет такого события (404 progression).
            DependencyUnavailableError: progression недоступен.
        """
        if viewer.token is None or viewer.token_rejected:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError("события доступны только пользователям", code=GUEST_FORBIDDEN)
        try:
            page = await self._progression.event_page(viewer.token, event_key)
        except UpstreamRejectedError as exc:
            if error := screen_error(exc, not_found="событие не найдено"):
                raise error from exc
            raise DependencyUnavailableError("progression отклонил запрос") from exc
        except UpstreamUnavailableError as exc:
            raise DependencyUnavailableError("progression недоступен") from exc
        return EventPageResult(page, ThemeCard(self._events.resolve_theme(page.theme_key)))


__all__ = ["EventSnapshot", "EventsContext", "GetEventPage"]
