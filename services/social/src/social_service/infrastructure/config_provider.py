"""Шов получения ``ConfigProvider`` (ADR 0053).

Действующую версию конфига читает БД-провайдер с коротким кэшем из контейнера друзей (F1);
контейнер комментариев и жалоб (F2) получает его отсюда.
"""

from __future__ import annotations

from gv_service import Infrastructure
from social_service.application.ports.config import ConfigProvider
from social_service.infrastructure.container import container_for


def config_provider_for(infra: Infrastructure) -> ConfigProvider:
    """Провайдер действующего конфига для процесса."""
    return container_for(infra).config
