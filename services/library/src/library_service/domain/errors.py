"""Ошибки медиатеки с машинными кодами ``library.*`` (план этапа 3b §2.5)."""

from __future__ import annotations

from gv_core.errors import ConflictError, InvalidInputError, NotFoundError, RateLimitedError


class ShelfNotFoundError(NotFoundError):
    """Полки нет или она чужая (404 ``library.shelf_not_found``): чужое неотличимо от
    несуществующего."""

    default_code = "library.shelf_not_found"

    def __init__(self, message: str = "полка не найдена") -> None:
        super().__init__(message)


class ShelvesLimitError(ConflictError):
    """Своих полок уже максимум (409 ``library.shelves_limit``, ``details.max``)."""

    default_code = "library.shelves_limit"

    def __init__(self, maximum: int) -> None:
        super().__init__("достигнут лимит своих полок", details={"max": maximum})


class ShelfFullError(ConflictError):
    """На полке максимум единиц (409 ``library.shelf_full``, ``details.max``)."""

    default_code = "library.shelf_full"

    def __init__(self, maximum: int) -> None:
        super().__init__("полка заполнена", details={"max": maximum})


class TitleInvalidError(InvalidInputError):
    """Название своей полки не прошло ``gv_text.ugc`` (422 ``library.title_invalid``).

    ``details.reason`` — код ``ugc.*``; сам текст в ответ не попадает.
    """

    default_code = "library.title_invalid"

    def __init__(self, reason: str, extra: dict[str, int] | None = None) -> None:
        super().__init__("недопустимое название полки", details={"reason": reason, **(extra or {})})


class ItemUnavailableError(InvalidInputError):
    """Единица неизвестна медиатеке или заблокирована (422 ``library.item_unavailable``)."""

    default_code = "library.item_unavailable"

    def __init__(self, message: str = "единица недоступна") -> None:
        super().__init__(message)


class DomainMismatchError(InvalidInputError):
    """Домен единицы не из доменов полки (422 ``library.domain_mismatch``)."""

    default_code = "library.domain_mismatch"

    def __init__(self, domain: str, allowed: tuple[str, ...]) -> None:
        super().__init__(
            "единицу этого домена нельзя сохранить на полку",
            details={"domain": domain, "allowed": list(allowed)},
        )


class LibraryRateLimitedError(RateLimitedError):
    """Превышен лимит частоты (429 ``library.rate_limited``, ``details.retry_after``)."""

    default_code = "library.rate_limited"

    def __init__(self, *, retry_after: int = 1) -> None:
        super().__init__("слишком много запросов", details={"retry_after": max(1, retry_after)})
