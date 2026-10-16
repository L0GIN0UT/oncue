"""Ошибки домена collections с машинными кодами (план этапа 3b §2.5, ADR 0028, 0030).

HTTP-статус выбирает ``gv_service.problems`` по базовому классу ``gv_core.errors``.
"""

from __future__ import annotations

from typing import Any

from gv_core.errors import (
    ConflictError,
    DependencyUnavailableError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)


class CollectionNotFoundError(NotFoundError):
    """Сборника нет, он удалён, чужой черновик или скрыт от текущего субъекта (404)."""

    default_code = "collections.not_found"

    def __init__(self, message: str = "сборник не найден") -> None:
        super().__init__(message)


class DraftConflictError(ConflictError):
    """``base_revision`` (``draft_revision``) не совпала с текущей ревизией черновика (409).

    ``details.current`` — актуальный ``DraftOut`` (D2): клиент показывает экран выбора.
    """

    default_code = "collections.draft_conflict"


class DraftsLimitError(ConflictError):
    """Неопубликованных сборников у автора уже ``drafts_max`` (409, ``details.max``)."""

    default_code = "collections.drafts_limit"


class CollectionHiddenError(ConflictError):
    """Сборник скрыт модератором: правка и публикация запрещены (409)."""

    default_code = "collections.hidden"

    def __init__(self, message: str = "сборник скрыт модератором") -> None:
        super().__init__(message)


class CollectionDeletedError(ConflictError):
    """Сборник удалён: опубликовать или сохранить его нельзя (409)."""

    default_code = "collections.deleted"

    def __init__(self, message: str = "сборник удалён") -> None:
        super().__init__(message)


class VersionConflictError(ConflictError):
    """``base_version`` кураторского сборника устарела (409, ``details.current_version``)."""

    default_code = "collections.version_conflict"


class NotCuratedError(ConflictError):
    """Admin-API правит только кураторские сборники; UGC правит автор (409)."""

    default_code = "collections.not_curated"

    def __init__(self, message: str = "сборник не кураторский") -> None:
        super().__init__(message)


class NotPublishedError(ConflictError):
    """Действие только для опубликованного сборника (скрыть, «рекомендуемое»), 409."""

    default_code = "collections.not_published"

    def __init__(self, message: str = "сборник ещё не опубликован") -> None:
        super().__init__(message)


class TitleInvalidError(InvalidInputError):
    """Название не прошло ``gv_text.ugc`` (422, ``details.reason`` — код ``ugc.*``)."""

    default_code = "collections.title_invalid"


class DraftInvalidError(InvalidInputError):
    """Черновик нарушает лимиты (422, ``details.errors`` — ``[{path, code}]``)."""

    default_code = "collections.draft_invalid"


class PublishInvalidError(InvalidInputError):
    """Сборник не проходит проверки публикации (422, ``details.errors`` — ``[{path, code}]``)."""

    default_code = "collections.publish_invalid"

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("сборник не проходит проверки публикации", details={"errors": errors})


class AuthorRestrictedError(ForbiddenError):
    """Автор ограничен модератором или санкцией ``CUSTOM_CONTENT`` (403)."""

    default_code = "collections.author_restricted"

    def __init__(self, message: str = "создание и правка сборников ограничены") -> None:
        super().__init__(message)


class OwnCollectionError(ForbiddenError):
    """Автор не может лайкнуть свой сборник (403)."""

    default_code = "collections.own_collection"

    def __init__(self, message: str = "свой сборник лайкнуть нельзя") -> None:
        super().__init__(message)


class PublishLimitError(RateLimitedError):
    """Публикаций за сутки больше ``publish_per_day`` (429, ``details.retry_after``)."""

    default_code = "collections.publish_limit"

    def __init__(self, *, retry_after: int, limit: int) -> None:
        super().__init__(
            "слишком много публикаций за сутки",
            details={"retry_after": retry_after, "max": limit},
        )


class CollectionsRateLimitedError(RateLimitedError):
    """Превышен лимит частоты лайков, избранного или поиска (429)."""

    default_code = "collections.rate_limited"

    def __init__(self, *, retry_after: int) -> None:
        super().__init__("слишком много запросов", details={"retry_after": retry_after})


class LimiterUnavailableError(DependencyUnavailableError):
    """Лимитер публикаций недоступен: публикация отклоняется (fail-closed, 503)."""

    default_code = "collections.limiter_unavailable"

    def __init__(self) -> None:
        super().__init__("публикация временно недоступна", details={"retry_after": 5})
