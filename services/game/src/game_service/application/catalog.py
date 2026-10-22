"""Каталог режимов: опубликованные конфиги → собранные правила ``ModeRules``."""

from __future__ import annotations

import structlog

from game_service.application.ports import ModeConfigSource
from game_service.application.rules import ModeRules, RulesFactory
from game_service.domain.errors import (
    ModeNotFoundError,
    ModeUnavailableError,
    ModeVersionNotFoundError,
)
from game_service.domain.modes import ConfigVersion, VersionStatus

log = structlog.get_logger(__name__)


class ModeCatalog:
    """Доступ к правилам режимов для игры (через кэш конфигов).

    Args:
        source: Опубликованные конфиги с кэшем.
        factory: Сборка правил по реестру плагинов процесса.
    """

    def __init__(self, source: ModeConfigSource, factory: RulesFactory) -> None:
        self.source = source
        self.factory = factory

    async def latest(self, key: str) -> tuple[ConfigVersion, ModeRules]:
        """Последняя опубликованная (не архивная) версия режима и её правила.

        Raises:
            ModeNotFoundError: режима нет или он в архиве.
            ModeUnavailableError: конфиг не собирается текущим реестром.
        """
        version = await self.source.latest(key)
        if version is None or version.status is not VersionStatus.PUBLISHED:
            raise ModeNotFoundError("режим не найден")
        return version, self.factory.build(version)

    async def rules(self, key: str, version: int) -> ModeRules:
        """Правила конкретной версии (сессии доигрываются на своей версии).

        Raises:
            ModeVersionNotFoundError: версии нет.
            ModeUnavailableError: конфиг не собирается текущим реестром.
        """
        found = await self.source.version(key, version)
        if found is None:
            raise ModeVersionNotFoundError("версия конфига не найдена")
        return self.factory.build(found)

    async def version_of(self, key: str, version: int) -> tuple[ConfigVersion, ModeRules]:
        """Опубликованная или архивная версия режима и её правила (план матча, ADR 0033).

        Raises:
            ModeNotFoundError: такой версии нет.
            ModeUnavailableError: конфиг не собирается текущим реестром.
        """
        found = await self.source.version(key, version)
        if found is None:
            raise ModeNotFoundError("версия режима не найдена")
        return found, self.factory.build(found)

    async def published(self, *, online: bool = False) -> list[ModeRules]:
        """Правила опубликованных режимов одного вида: по умолчанию — соло (их играет game:
        каталог, дейлики, режимы сборника), при ``online`` — онлайн (играет match, ADR 0032).

        Несобираемые пропускаются с ошибкой в логе.
        """
        result = []
        for version in await self.source.published():
            try:
                rules = self.factory.build(version)
            except ModeUnavailableError:
                log.exception(
                    "game.mode_unavailable", mode_key=version.key, version=version.version
                )
                continue
            if rules.is_online == online:
                result.append(rules)
        return sorted(result, key=lambda r: r.key)
