"""Обработчики доменных событий других сервисов.

Чтобы подписаться на событие: добавить топик в ``CONSUMED_TOPICS`` и зарегистрировать
обработчик в ``build_event_handlers`` (обработчик вызывает use-case из application).
Обработчики потоков живут в отдельных модулях рядом: друзья - ``friends.py``, комментарии,
жалобы и шкалы порядочности - ``moderation.py``.

``identity.UserDeleted`` и ``match.MatchFinished`` слушают обе области, а реестр не принимает
двух обработчиков на тип события: обработчик модерации вызывает после себя обработчик друзей
в той же транзакции.
"""

from __future__ import annotations

from gv_messaging import EventHandlerRegistry
from gv_service import Infrastructure
from social_service.infrastructure.messaging.friends import (
    FRIENDS_TOPICS,
    MATCH_FINISHED,
    USER_DELETED,
    register_friends_handlers,
)
from social_service.infrastructure.messaging.moderation import (
    MODERATION_TOPICS,
    register_moderation_handlers,
)

CONSUMED_TOPICS: tuple[str, ...] = tuple(dict.fromkeys((*FRIENDS_TOPICS, *MODERATION_TOPICS)))


def build_event_handlers(infra: Infrastructure) -> EventHandlerRegistry:
    """Реестр обработчиков событий сервиса."""
    registry = EventHandlerRegistry()
    shared = register_friends_handlers(registry, infra, include_shared=False)
    register_moderation_handlers(
        registry,
        infra,
        then={USER_DELETED: shared.user_deleted, MATCH_FINISHED: shared.match_finished},
    )
    return registry
