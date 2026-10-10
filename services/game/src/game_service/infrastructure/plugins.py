"""Реестр плагинов процесса game и сводная JSON Schema конфига для редактора админки.

Встроенные плагины регистрирует ядро (``gv_game_kernel.builtin.register_builtin``).
``collection`` в game на этапе 2 не регистрируется (проекции сборников — этап 3), поэтому
конфиг с ним не проходит валидацию.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from gv_game_kernel import (
    AnswerMatcher,
    ClueType,
    ModeConfig,
    PartialRule,
    PluginRegistry,
    Scorer,
    Source,
)
from gv_game_kernel.builtin import register_builtin

EXCLUDED_SOURCES = frozenset({"collection"})


class CatalogRegistry(PluginRegistry):
    """``PluginRegistry``, который помнит зарегистрированные плагины (для схемы редактора)."""

    def __init__(self, *, excluded_sources: frozenset[str] = EXCLUDED_SOURCES) -> None:
        super().__init__()
        self._excluded_sources = excluded_sources
        self.schemas: dict[str, dict[str, type[BaseModel]]] = {
            "clue": {},
            "source": {},
            "matcher": {},
            "scoring": {},
        }
        self.partial_kinds: list[str] = []

    def register_clue(self, plugin: ClueType) -> None:
        """Тип загадки."""
        super().register_clue(plugin)
        self.schemas["clue"][plugin.key] = plugin.params_schema

    def register_source(self, plugin: Source) -> None:
        """Источник (кроме исключённых на этом этапе)."""
        if plugin.key in self._excluded_sources:
            return
        super().register_source(plugin)
        self.schemas["source"][plugin.key] = plugin.params_schema

    def register_matcher(self, plugin: AnswerMatcher) -> None:
        """Matcher."""
        super().register_matcher(plugin)
        self.schemas["matcher"][plugin.key] = plugin.params_schema

    def register_partial(self, plugin: PartialRule) -> None:
        """Правило частичного ответа."""
        super().register_partial(plugin)
        self.partial_kinds.append(plugin.kind)

    def register_scorer(self, plugin: Scorer) -> None:
        """Подсчёт очков."""
        super().register_scorer(plugin)
        self.schemas["scoring"][plugin.key] = plugin.params_schema

    def editor_schema(self) -> dict[str, Any]:
        """Сводная схема: модель конфига + схемы ``params`` каждого плагина по секциям."""
        return {
            "mode_config": ModeConfig.model_json_schema(),
            "plugins": {
                section: {key: model.model_json_schema() for key, model in sorted(items.items())}
                for section, items in self.schemas.items()
            },
            "partial": sorted(self.partial_kinds),
        }


def build_registry() -> CatalogRegistry:
    """Реестр процесса со встроенными плагинами ядра."""
    registry = CatalogRegistry()
    register_builtin(registry)
    return registry
