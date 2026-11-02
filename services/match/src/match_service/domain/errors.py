"""Ошибки match: коды ``match.*`` (контракт ``contracts/openapi/match.json``).

Команды комнаты применяет владелец в процессе ``rooms`` (ADR 0034), а ответ клиенту отдаёт
``api``: ошибка переезжает между процессами словарём ``{status, code, message, details}``.
Поэтому класс ошибки однозначно задаётся HTTP-статусом (``error_for_status``) — а код
``match.*`` несёт смысл для клиента.
"""

from __future__ import annotations

from typing import Any

from gv_core.errors import (
    AppError,
    ConflictError,
    DependencyUnavailableError,
    DomainError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)

ROOM_NOT_FOUND = "match.room_not_found"
NOT_PARTICIPANT = "match.not_participant"
PHASE_MISMATCH = "match.phase_mismatch"
RATE_LIMITED = "match.rate_limited"
ROOMS_UNAVAILABLE = "match.rooms_unavailable"
MODE_NOT_FOUND = "match.mode_not_found"
MODE_NOT_ONLINE = "match.mode_not_online"
SETTING_NOT_ALLOWED = "match.setting_not_allowed"
COLLECTION_REQUIRED = "match.collection_required"
TOO_MANY_COLLECTIONS = "match.too_many_collections"
COLLECTION_NOT_FOUND = "match.collection_not_found"
RANKED_FORBIDDEN = "match.ranked_forbidden"
ACTIVE_ROOM_CONFLICT = "match.active_room_conflict"
GAME_UNAVAILABLE = "match.game_unavailable"
START_UNAVAILABLE = "match.start_unavailable"
ROOM_FULL = "match.room_full"
MATCH_IN_PROGRESS = "match.match_in_progress"
KICKED = "match.kicked"
NOT_HOST = "match.not_host"
PLAYERS_OVER_LIMIT = "match.players_over_limit"
CANNOT_KICK_SELF = "match.cannot_kick_self"
PLAYER_NOT_FOUND = "match.player_not_found"
NOT_ALL_READY = "match.not_all_ready"
NOT_ENOUGH_PLAYERS = "match.not_enough_players"
POOL_TOO_SMALL = "match.pool_too_small"
CONTENT_UNAVAILABLE = "match.content_unavailable"
LEVEL_LOCKED = "match.level_locked"
ANSWER_KIND_UNSUPPORTED = "match.answer_kind_unsupported"
OPTION_UNKNOWN = "match.option_unknown"
EMOTE_UNKNOWN = "match.emote_unknown"
CHAT_MUTED = "match.chat_muted"
ONLINE_BANNED = "match.online_banned"
SANCTIONS_UNAVAILABLE = "match.sanctions_unavailable"
CHAT_DISABLED = "match.chat_disabled"
CHAT_REJECTED = "match.chat_rejected"
ROOM_NAME_REJECTED = "match.room_name_rejected"
MATCH_NOT_FOUND = "match.match_not_found"
CANNOT_RATE_SELF = "match.cannot_rate_self"
ALREADY_RATED = "match.already_rated"
ROOM_CLOSED = "match.room_closed"
COMMAND_UNKNOWN = "match.command_unknown"
LADDER_NOT_FOUND = "match.ladder_not_found"
QUEUE_FORMAT_UNAVAILABLE = "match.queue_format_unavailable"
NOT_IN_QUEUE = "match.not_in_queue"
QUEUE_MATCHING = "match.queue_matching"


class MatchNotFoundError(NotFoundError):
    """404: комната, игрок или матч не найдены."""

    default_code = ROOM_NOT_FOUND


def room_gone(close_reason: str | None) -> MatchNotFoundError:
    """404 «комнаты нет или она закрыта» с причиной закрытия в ``details.reason``.

    Причину (``empty``, ``empty_timeout``, ``idle``, ``finished``, ``start_failed`` ...) знает
    только закрытая комната, пока жив её снимок; нет комнаты вовсе - ``not_found``. Фронт
    показывает понятное сообщение вместо безликого «пусто» (пункт 22 замечаний тестирования).
    """
    return MatchNotFoundError(
        "комнаты нет или она закрыта",
        code=ROOM_NOT_FOUND,
        details={"reason": close_reason or "not_found"},
    )


class MatchForbiddenError(ForbiddenError):
    """403: нет прав на действие в комнате."""

    default_code = NOT_PARTICIPANT


class MatchConflictError(ConflictError):
    """409: действие не подходит к состоянию комнаты."""

    default_code = PHASE_MISMATCH


class MatchInvalidError(InvalidInputError):
    """422: недопустимые данные команды."""

    default_code = SETTING_NOT_ALLOWED


class MatchRateLimitedError(RateLimitedError):
    """429: лимит частоты; ``details.retry_after`` — через сколько секунд повторить."""

    default_code = RATE_LIMITED

    def __init__(self, message: str = "слишком много запросов", *, retry_after: int = 1) -> None:
        super().__init__(message, details={"retry_after": max(1, retry_after)})


class MatchUnavailableError(DependencyUnavailableError):
    """503: зависимость (game, Redis комнат) временно недоступна."""

    default_code = ROOMS_UNAVAILABLE


_BY_STATUS: dict[int, type[AppError]] = {
    403: MatchForbiddenError,
    404: MatchNotFoundError,
    409: MatchConflictError,
    422: MatchInvalidError,
    503: MatchUnavailableError,
}
_RATE_LIMITED_STATUS = 429


def error_for_status(
    status: int, code: str, message: str, details: dict[str, Any] | None = None
) -> AppError:
    """Восстановить ошибку из ответа владельца комнаты (статус → класс, код — как есть)."""
    if status == _RATE_LIMITED_STATUS:
        retry = (details or {}).get("retry_after", 1)
        err: AppError = MatchRateLimitedError(message, retry_after=int(retry))
        err.code = code
        return err
    cls = _BY_STATUS.get(status, MatchUnavailableError)
    return cls(message, code=code, details=details)


def status_of(error: AppError) -> int:
    """HTTP-статус ошибки команды (обратное к ``error_for_status``)."""
    for base, status in _STATUS_OF:
        if isinstance(error, base):
            return status
    return 503


_STATUS_OF: tuple[tuple[type[AppError], int], ...] = (
    (ForbiddenError, 403),
    (NotFoundError, 404),
    (ConflictError, 409),
    (InvalidInputError, 422),
    (DomainError, 422),
    (RateLimitedError, _RATE_LIMITED_STATUS),
)


_OVERRIDE_FIELDS = {"clue": "clue_type"}


def override_details(game_code: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Подробности отказа game по переопределениям лобби (ADR 0070) в терминах настроек match.

    ``game.override_not_allowed`` — значения нет в разрешённых шаблоном (``field`` —
    настройка комнаты); ``game.override_invalid`` — сочетание не прошло валидатор ядра
    (``errors`` — ``[{path, code}]``, без данных контента).
    """
    raw = raw or {}
    if game_code == "game.override_not_allowed":
        field = str(raw.get("field", ""))
        return {"field": _OVERRIDE_FIELDS.get(field, field), "reason": "not_allowed"}
    return {"field": "overrides", "reason": "incompatible", "errors": raw.get("errors", [])}
