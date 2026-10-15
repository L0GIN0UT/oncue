"""Встроенные правила завершения сессии (ADR 0023).

* ``all_rounds`` — сессия идёт все ``session.rounds`` раундов; оно же действует, если
  ``session.end`` в конфиге не задан;
* ``lives`` — сессия заканчивается после ``count`` проигранных раундов (марафон — 3,
  «больше/меньше» — 1). Проигран любой раунд без точного ответа, кроме ``void``:
  аннулированный раунд (ассет недоступен) — не вина игрока.
"""

# ruff: noqa: ARG002 — методы реализуют протокол EndRule, не всем нужны все аргументы

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, ClassVar

from pydantic import BaseModel, Field, StrictInt

from gv_game_kernel.builtin._base import PluginParams, typed
from gv_game_kernel.round import RoundOutcome, RoundResult

ALL_ROUNDS = "all_rounds"
"""Ключ правила по умолчанию."""

_NOT_LOST = frozenset({RoundResult.EXACT, RoundResult.VOID})


def losses(history: Sequence[RoundOutcome]) -> int:
    """Число проигранных раундов: всё, кроме ``exact`` и ``void``."""
    return sum(1 for outcome in history if outcome.result not in _NOT_LOST)


class AllRoundsParams(PluginParams):
    """Параметры ``all_rounds`` (``contracts/modes/params/end.all_rounds.v1.json``): нет."""


class AllRoundsEnd:
    """Досрочного завершения нет: сессия заканчивается, когда сыграны все раунды."""

    key: ClassVar[str] = ALL_ROUNDS
    params_schema: ClassVar[type[BaseModel]] = AllRoundsParams
    ends_early: ClassVar[bool] = False

    def should_end(self, history: Sequence[RoundOutcome], params: BaseModel) -> bool:
        """Никогда — конец сессии определяет число раундов."""
        return False

    def lives_left(self, history: Sequence[RoundOutcome], params: BaseModel) -> int | None:
        """Жизней нет."""
        return None


class LivesParams(PluginParams):
    """Параметры ``lives`` (``contracts/modes/params/end.lives.v1.json``)."""

    count: Annotated[StrictInt, Field(ge=1, le=10)]


class LivesEnd:
    """«Жизни»: сессия заканчивается после ``count`` проигранных раундов."""

    key: ClassVar[str] = "lives"
    params_schema: ClassVar[type[BaseModel]] = LivesParams
    ends_early: ClassVar[bool] = True

    def should_end(self, history: Sequence[RoundOutcome], params: BaseModel) -> bool:
        """Жизни кончились."""
        return losses(history) >= typed(params, LivesParams).count

    def lives_left(self, history: Sequence[RoundOutcome], params: BaseModel) -> int:
        """``count`` минус проигранные раунды (не меньше 0)."""
        return max(0, typed(params, LivesParams).count - losses(history))
