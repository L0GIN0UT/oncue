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
ROUND_NOT_FINISHED = "game.round_not_finished"
SKIP_NOT_ALLOWED = "game.skip_not_allowed"
GIVE_UP_NOT_ALLOWED = "game.give_up_not_allowed"
POOL_TOO_SMALL = "game.pool_too_small"
PRESET_NOT_FOUND = "game.preset_not_found"


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


class RoundNotFinishedError(ConflictError, RuleViolation):
    """Операция требует завершённого раунда: ревил, итог для Scorer (409)."""

    default_code = ROUND_NOT_FINISHED


class ActionNotAllowedError(ForbiddenError, RuleViolation):
    """Ход запрещён конфигом режима: пропуск (``game.skip_not_allowed``) или сдача
    (``game.give_up_not_allowed``) — 403."""

    default_code = SKIP_NOT_ALLOWED


class PoolTooSmallError(DomainError):
    """В пуле меньше играбельных единиц, чем нужно раундов (422 ``game.pool_too_small``).

    ``details = {"available": int, "required": int}``. game для дейлика переводит набор в
    ``failed`` и отвечает ``503 game.daily_not_ready``.
    """

    default_code = POOL_TOO_SMALL


class PresetNotFoundError(InvalidInputError):
    """У режима нет пресета с таким ``preset_id`` (422 ``game.preset_not_found``)."""

    default_code = PRESET_NOT_FOUND
