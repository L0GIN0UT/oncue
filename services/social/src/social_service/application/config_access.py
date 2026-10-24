"""Доступ use-cases к действующему конфигу с переводом отказа в 503 (ADR 0053)."""

from __future__ import annotations

from gv_core.errors import DependencyUnavailableError
from social_service.application.ports.config import ConfigProvider, ConfigUnavailableError
from social_service.domain.config import SocialConfig

CONFIG_UNAVAILABLE = "social.config_unavailable"
RETRY_AFTER_S = 5


async def current_config(provider: ConfigProvider) -> SocialConfig:
    """Действующий конфиг.

    Raises:
        DependencyUnavailableError: ``social.config_unavailable`` (503) - версии нет или БД
            недоступна; операции, зависящие от конфига, закрыты (fail-closed).
    """
    try:
        return await provider.current()
    except ConfigUnavailableError as exc:
        raise DependencyUnavailableError(
            "конфиг social недоступен",
            code=CONFIG_UNAVAILABLE,
            details={"retry_after": RETRY_AFTER_S},
        ) from exc
