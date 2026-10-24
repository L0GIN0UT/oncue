"""Обработчики доменных событий других сервисов.

Чтобы подписаться на событие: добавить топик в ``CONSUMED_TOPICS`` и зарегистрировать
обработчик в ``build_event_handlers`` (обработчик вызывает use-case из application).
Обработчики потоков живут в отдельных модулях рядом: друзья - ``friends.py``.
"""

from __future__ import annotations

from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure
from social_service.infrastructure.messaging.friends import (
    FRIENDS_TOPICS,
    register_friends_handlers,
)

CONSUMED_TOPICS: tuple[str, ...] = (*FRIENDS_TOPICS,)


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков событий сервиса."""
    registry = EventHandlerRegistry()
    register_friends_handlers(registry, infra)
    return registry
