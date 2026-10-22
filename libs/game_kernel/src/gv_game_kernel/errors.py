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
ANSWER_KIND_UNSUPPORTED = "game.answer_kind_unsupported"
OPTION_UNKNOWN = "game.option_unknown"
SUBJECT_REQUIRED = "game.subject_required"
TOO_MANY_SUBJECTS = "game.too_many_subjects"
COLLECTION_REQUIRED = "game.collection_required"
ROUNDS_NOT_ALLOWED = "game.rounds_not_allowed"
MODE_NOT_SOLO = "game.mode_not_solo"


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


class AnswerKindError(InvalidInputError, RuleViolation):
    """Ответ не того вида, что принимает формат режима (422 ``game.answer_kind_unsupported``).

    Например, текст в режиме ``pick`` или ``option_id`` в классическом режиме.
    ``details = {"kind": "text", "allowed": ["option"]}``.
    """

    default_code = ANSWER_KIND_UNSUPPORTED


class OptionNotFoundError(InvalidInputError, RuleViolation):
    """Выбран вариант, которого нет в раунде (422 ``game.option_unknown``)."""

    default_code = OPTION_UNKNOWN


class SubjectRequiredError(InvalidInputError):
    """Источнику по персонам не передана ни одна персона (422 ``game.subject_required``)."""

    default_code = SUBJECT_REQUIRED


class TooManySubjectsError(InvalidInputError):
    """Персон больше, чем ``max_subjects`` режима (422 ``game.too_many_subjects``).

    ``details = {"given": int, "max": int}``.
    """

    default_code = TOO_MANY_SUBJECTS


class CollectionRequiredError(InvalidInputError):
    """Источнику ``collection`` не передан сборник (422 ``game.collection_required``)."""

    default_code = COLLECTION_REQUIRED


class RoundsNotAllowedError(InvalidInputError):
    """Запрошенного числа раундов нет в ``round_options`` режима (422 ``game.rounds_not_allowed``).

    ``details = {"requested": int | "all", "options": [...]}``.
    """

    default_code = ROUNDS_NOT_ALLOWED


class ModeNotSoloError(InvalidInputError):
    """Онлайн-режим нельзя играть соло: его играет match (422 ``game.mode_not_solo``, ADR 0032)."""

    default_code = MODE_NOT_SOLO
