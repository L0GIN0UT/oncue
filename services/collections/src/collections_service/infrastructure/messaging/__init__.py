"""Обработчики доменных событий других сервисов.

Чтобы подписаться на событие: добавить топик в ``CONSUMED_TOPICS`` и зарегистрировать
обработчик в ``build_event_handlers`` (обработчик вызывает use-case из application).
"""

from __future__ import annotations

from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure

CONSUMED_TOPICS: tuple[str, ...] = ()


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:  # noqa: ARG001 — DI для use-cases
    """Реестр обработчиков событий сервиса."""
    return EventHandlerRegistry()
