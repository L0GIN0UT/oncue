"""Ошибки домена media-pipeline с машинными кодами (контракт internal/admin API, §2.5 плана)."""

from __future__ import annotations

from gv_core.errors import AppError, ConflictError, InvalidInputError, NotFoundError


class AssetSetNotFoundError(NotFoundError):
    """Набор ассетов не найден (404)."""

    default_code = "asset.not_found"


class AssetNotReadyError(ConflictError):
    """Набор ещё генерируется или генерация не удалась (409)."""

    default_code = "asset.not_ready"


class AssetBlockedError(AppError):
    """Единица заблокирована, ассеты выключены (410 Gone).

    В ``gv_service.problems`` нет класса для 410, поэтому API отображает её явно.
    """

    default_code = "asset.blocked"


class LevelOutOfRangeError(InvalidInputError):
    """Уровень вне диапазона набора (422)."""

    default_code = "asset.level_out_of_range"


class MediaItemNotFoundError(NotFoundError):
    """Медиа-единица ещё не известна сервису (нет события из catalog)."""

    default_code = "media_item.not_found"


class InvalidProfileError(InvalidInputError):
    """Профиль уровней не прошёл проверку (ошибка конфигурации)."""

    default_code = "profile.invalid"


class UnsupportedSourceError(InvalidInputError):
    """Файл-источник не того типа: сигнатура не распознана или не подходит к ``kind``."""

    default_code = "source.unsupported_type"


class SourceTooLargeError(InvalidInputError):
    """Файл-источник больше лимита."""

    default_code = "source.too_large"
