"""Общее для use-cases начисления: действующий конфиг и контракт входа."""

from __future__ import annotations

from datetime import datetime

from gv_core.errors import DependencyUnavailableError
from progression_service.application.ports import ProgressionUnitOfWork, PublishedConfig

CONFIG_UNAVAILABLE = "progression.config_unavailable"


class ConfigUnavailableError(DependencyUnavailableError):
    """Нет опубликованной версии конфига: событие откладывается (повтор), а не в DLQ."""

    default_code = CONFIG_UNAVAILABLE

    def __init__(self) -> None:
        super().__init__("нет опубликованного конфига progression", code=CONFIG_UNAVAILABLE)


class AccrualContractError(Exception):
    """Данные события противоречат контракту/правилам (повтор бессмыслен → DLQ)."""


async def published_config(uow: ProgressionUnitOfWork) -> PublishedConfig:
    """Действующая версия конфига.

    Raises:
        ConfigUnavailableError: опубликованной версии нет (повторимо).
    """
    published = await uow.configs.published()
    if published is None:
        raise ConfigUnavailableError
    return published


async def config_at(uow: ProgressionUnitOfWork, moment: datetime) -> PublishedConfig:
    """Версия конфига на момент события (``finished_at``, ADR 0042), а не на момент обработки.

    Raises:
        ConfigUnavailableError: опубликованных версий нет (повторимо).
    """
    published = await uow.configs.effective_at(moment)
    if published is None:
        raise ConfigUnavailableError
    return published
