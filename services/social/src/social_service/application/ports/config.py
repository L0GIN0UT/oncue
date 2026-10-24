"""Порт чтения действующего конфига social (ADR 0053)."""

from __future__ import annotations

from typing import Protocol

from social_service.domain.config import SocialConfig


class ConfigUnavailableError(Exception):
    """Действующей версии конфига нет (ещё не опубликована) - 503 ``social.config_unavailable``."""


class ConfigProvider(Protocol):
    """Действующая версия конфига; реализация - БД с коротким кэшем в процессе (поток F1)."""

    async def current(self) -> SocialConfig:
        """Действующая версия.

        Raises:
            ConfigUnavailableError: версии нет или БД недоступна (fail-closed).
        """
        ...
