"""Use-cases admin-API social для слоя API (собираются в composition root ``main.py``)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from social_service.application.cases import CaseService
from social_service.application.config_admin import ConfigCommands, ConfigQueries
from social_service.application.win_trading import WinTradingService


@dataclass(frozen=True, slots=True)
class AdminServices:
    """Use-cases ручек очереди модерации и конфига."""

    cases: CaseService
    config_commands: Callable[[], ConfigCommands]
    config_queries: Callable[[], ConfigQueries]
    config_changed: Callable[[], None]
    """Вызывается после публикации/отката: сбрасывает кэш конфига в этом процессе."""
    win_trading: WinTradingService | None = None
    """Сигналы win-trading для модератора (ADR 0068)."""
