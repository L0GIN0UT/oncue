"""Обработчики доменных событий других сервисов.

Чтобы подписаться на событие: добавить топик в ``CONSUMED_TOPICS`` и зарегистрировать
обработчик в ``build_event_handlers`` (обработчик вызывает use-case из application).
Комментарии, жалобы и шкалы порядочности - ``moderation.py``.
"""

from __future__ import annotations

from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure
from social_service.infrastructure.messaging.moderation import (
    MODERATION_TOPICS,
    register_moderation_handlers,
)

CONSUMED_TOPICS: tuple[str, ...] = (*MODERATION_TOPICS,)


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков событий сервиса."""
    registry = EventHandlerRegistry()
    register_moderation_handlers(registry, infra)
    return registry
