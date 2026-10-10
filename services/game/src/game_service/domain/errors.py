"""Ошибки домена game с машинными кодами (план этапа 2, §2.6).

HTTP-статус выбирает ``gv_service.problems`` по базовому классу ``gv_core.errors``.
"""

from __future__ import annotations

from typing import Any

from gv_core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    GoneError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)


class ModeNotFoundError(NotFoundError):
    """Режима нет или он не опубликован (404 ``game.mode_not_found``)."""

    default_code = "game.mode_not_found"


class ModeVersionNotFoundError(NotFoundError):
    """Нет такой версии конфига (404 ``game.mode_version_not_found``)."""

    default_code = "game.mode_version_not_found"


class ModeUnavailableError(DependencyUnavailableError):
    """Опубликованный конфиг не собирается текущим реестром плагинов (503)."""

    default_code = "game.mode_unavailable"


class SessionNotFoundError(NotFoundError):
    """Сессии нет или она чужая — ответ одинаковый (404 ``game.session_not_found``)."""

    default_code = "game.session_not_found"


class SessionNotActiveError(ConflictError):
    """Ход в завершённой или брошенной сессии (409 ``game.session_finished``)."""

    default_code = "game.session_finished"


class SessionNotFinishedError(ConflictError):
    """Итог запрошен до завершения сессии (409 ``game.session_not_finished``)."""

    default_code = "game.session_not_finished"


class RoundNotActiveError(ConflictError):
    """Раунд ещё не начат (409 ``game.round_not_active``)."""

    default_code = "game.round_not_active"


class DesyncError(ConflictError):
    """``expected_version`` устарела (409).

    Формат ``details`` зафиксирован контрактом: ``{"session": <SessionSnapshot>}`` —
    клиент применяет снимок без повторного ``GET``.
    """

    default_code = "game.desync"


class DailyAlreadyPlayedError(ConflictError):
    """Дейлик уже сыгран (409 ``game.daily_already_played``, ``details.session_id``)."""

    default_code = "game.daily_already_played"


class GuestNotAllowedError(ForbiddenError):
    """Режим не пускает гостей (403 ``game.guest_not_allowed``)."""

    default_code = "game.guest_not_allowed"


class DailyNotReadyError(DependencyUnavailableError):
    """Набора дня нет и создать его не удалось (503 ``game.daily_not_ready``)."""

    default_code = "game.daily_not_ready"


class PoolEmptyError(DependencyUnavailableError):
    """В пуле режима нет играбельных единиц (503 ``game.pool_empty``)."""

    default_code = "game.pool_empty"


class PresetNotFoundError(InvalidInputError):
    """У режима нет такого пресета (422 ``game.preset_not_found``)."""

    default_code = "game.preset_not_found"


class AssetUnavailableError(GoneError):
    """Ассет раунда выключен — раунд аннулирован (410 ``game.asset_unavailable``)."""

    default_code = "game.asset_unavailable"


class AssetsUnavailableError(DependencyUnavailableError):
    """media-pipeline временно не выдаёт ссылки (503 ``game.assets_unavailable``)."""

    default_code = "game.assets_unavailable"


class GameRateLimitedError(RateLimitedError):
    """Превышен лимит частоты (429 ``game.rate_limited``)."""

    default_code = "game.rate_limited"

    def __init__(self, message: str = "слишком много запросов", *, retry_after: int = 1) -> None:
        super().__init__(message, details={"retry_after": max(1, retry_after)})


class DraftConflictError(ConflictError):
    """Ревизия черновика устарела (409 ``game.draft_conflict``)."""

    default_code = "game.draft_conflict"


class DraftNotFoundError(NotFoundError):
    """Черновика нет (404 ``game.draft_not_found``)."""

    default_code = "game.draft_not_found"


class ModeNotPublishedError(ConflictError):
    """Действие требует опубликованной версии (409 ``game.mode_not_published``)."""

    default_code = "game.mode_not_published"


class ConfigTooLargeError(InvalidInputError):
    """Тело конфига больше ``GV_GAME__MODE_CONFIG_MAX_BYTES`` (422)."""

    default_code = "game.config_too_large"


class DailySetNotFoundError(NotFoundError):
    """Набора дня нет (404 ``game.daily_set_not_found``)."""

    default_code = "game.daily_set_not_found"


class DailyLockedError(ConflictError):
    """День начался — набор только для чтения (409 ``game.daily_locked``)."""

    default_code = "game.daily_locked"


class ItemNotPlayableError(ConflictError):
    """Единица не играбельна под параметры режима (409 ``game.item_not_playable``)."""

    default_code = "game.item_not_playable"


class ItemDuplicateError(ConflictError):
    """Единица уже есть в наборе (409 ``game.item_duplicate``)."""

    default_code = "game.item_duplicate"


class DailyIndexOutOfRangeError(InvalidInputError):
    """Номер единицы вне набора (422 ``game.daily_index_out_of_range``)."""

    default_code = "game.daily_index_out_of_range"


def desync(snapshot: dict[str, Any]) -> DesyncError:
    """Ошибка рассинхронизации с актуальным снимком в ``details.session``."""
    return DesyncError("версия сессии устарела", details={"session": snapshot})
