"""Шов получения ``ConfigProvider`` (ADR 0053).

ВРЕМЕННАЯ заглушка потока F2: пока нет адаптера БД (его пишет поток F1), провайдер всегда
сообщает «конфиг недоступен» - ручки отвечают 503 ``social.config_unavailable``. Поток F1
заменяет тело ``config_provider_for`` на БД-адаптер с кэшем; контейнер (F2) не меняется.
"""

from __future__ import annotations

from gv_service import Infrastructure
from social_service.application.ports.config import ConfigProvider, ConfigUnavailableError
from social_service.domain.config import SocialConfig


class _UnavailableConfig:
    async def current(self) -> SocialConfig:
        raise ConfigUnavailableError("адаптер конфига ещё не подключён")


def config_provider_for(infra: Infrastructure) -> ConfigProvider:  # noqa: ARG001 - шов F1
    """Провайдер действующего конфига для процесса."""
    return _UnavailableConfig()
