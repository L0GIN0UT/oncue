"""Ошибки домена media-pipeline с машинными кодами (контракт internal/admin API, §2.5 плана)."""

from __future__ import annotations

from gv_core.errors import (
    ConflictError,
    ForbiddenError,
    GoneError,
    InvalidInputError,
    NotFoundError,
)


class AssetSetNotFoundError(NotFoundError):
    """Набор ассетов не найден (404)."""

    default_code = "asset.not_found"


class AssetNotReadyError(ConflictError):
    """Набор ещё генерируется или генерация не удалась (409)."""

    default_code = "asset.not_ready"


class AssetBlockedError(GoneError):
    """Единица заблокирована, ассеты выключены (410 Gone)."""

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


class SourceNotFoundError(NotFoundError):
    """Исходник не найден (404)."""

    default_code = "source.not_found"


class CollectionContentInvalidError(InvalidInputError):
    """Свои загадки сборника нарушают контракт: эмодзи вне строгого набора RGI или длиннее
    лимита, единица повторяется (защита в глубину, ADR 0029). Повтор события не поможет."""

    default_code = "collection.content_invalid"


class InvalidUgcImageError(InvalidInputError):
    """Файл не прошёл проверку (422): тип, кадры, размеры, повреждение.

    Причину клиенту не раскрываем: детали декодера - внутренние.
    """

    default_code = "ugc.invalid_image"


class UgcTooLargeError(InvalidInputError):
    """Файл больше лимита назначения (API отвечает 413)."""

    default_code = "ugc.too_large"


class UgcPurposeUnknownError(InvalidInputError):
    """Назначения нет в конфиге (422)."""

    default_code = "ugc.purpose_unknown"


class UgcReasonUnknownError(InvalidInputError):
    """Кода причины нет в списке конфига (422)."""

    default_code = "ugc.reason_unknown"


class UgcClientForbiddenError(ForbiddenError):
    """Сервису-клиенту это назначение запрещено (403)."""

    default_code = "ugc.client_forbidden"


class UgcNotFoundError(NotFoundError):
    """UGC-ассет не найден (404)."""

    default_code = "ugc.not_found"


class UgcNotPendingError(ConflictError):
    """Решение невозможно из текущего статуса (409)."""

    default_code = "ugc.not_pending"


class PackageNotFoundError(NotFoundError):
    """Пакет пакетной загрузки не найден (404)."""

    default_code = "package.not_found"
