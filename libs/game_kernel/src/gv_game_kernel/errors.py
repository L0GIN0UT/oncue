"""Ошибки ядра движка.

Наследуются от ``gv_core.errors``: HTTP-статус выбирает ``gv_service.problems`` по классу
(первый подходящий в таблице), поэтому конкретные нарушения правил раунда наследуют ещё и
нужную ошибку ``gv_core`` — 403 для закрытого уровня, 409 для завершённого раунда.
"""

from __future__ import annotations

from typing import Any

from gv_core.errors import ConflictError, DomainError, ForbiddenError, InvalidInputError

CONFIG_INVALID = "game.config_invalid"
LEVEL_LOCKED = "game.level_locked"
ROUND_FINISHED = "game.round_finished"


class ConfigError(InvalidInputError):
    """Конфиг режима не проходит схему или несовместим с реестром плагинов (422).

    ``details["errors"]`` — список ``{"path", "code"}`` (см. ``ConfigIssue``).
    """

    default_code = CONFIG_INVALID

    def __init__(
        self,
        message: str = "конфиг режима невалиден",
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, details=details)


class RuleViolation(DomainError):  # noqa: N818 — имя из плана этапа 2 (§2.3)
    """Ход нарушает правила раунда (общий предок нарушений правил)."""

    default_code = "game.rule_violated"


class LevelLockedError(ForbiddenError, RuleViolation):
    """Запрошен уровень загадки выше открытого (403 ``game.level_locked``)."""

    default_code = LEVEL_LOCKED


class RoundFinishedError(ConflictError, RuleViolation):
    """Ход в уже завершённом раунде (409 ``game.round_finished``)."""

    default_code = ROUND_FINISHED
