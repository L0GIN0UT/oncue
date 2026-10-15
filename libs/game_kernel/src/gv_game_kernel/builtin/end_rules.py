"""Встроенные правила завершения сессии (ADR 0023).

``all_rounds`` — сессия идёт все ``session.rounds`` раундов; оно же действует, если
``session.end`` в конфиге не задан.
"""

# ruff: noqa: ARG002 — методы реализуют протокол EndRule, не всем нужны все аргументы

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from pydantic import BaseModel

from gv_game_kernel.builtin._base import PluginParams
from gv_game_kernel.round import RoundOutcome

ALL_ROUNDS = "all_rounds"
"""Ключ правила по умолчанию."""


class AllRoundsParams(PluginParams):
    """Параметры ``all_rounds`` (``contracts/modes/params/end.all_rounds.v1.json``): нет."""


class AllRoundsEnd:
    """Досрочного завершения нет: сессия заканчивается, когда сыграны все раунды."""

    key: ClassVar[str] = ALL_ROUNDS
    params_schema: ClassVar[type[BaseModel]] = AllRoundsParams

    def should_end(self, history: Sequence[RoundOutcome], params: BaseModel) -> bool:
        """Никогда — конец сессии определяет число раундов."""
        return False

    def lives_left(self, history: Sequence[RoundOutcome], params: BaseModel) -> int | None:
        """Жизней нет."""
        return None
