"""Иерархия ошибок приложения.

Домен и use-cases бросают только наследников ``AppError``. Слой API превращает их в
RFC 9457 Problem Details в одном месте (``gv_service.problems``) — домен про HTTP не знает.

У каждой ошибки есть машинный ``code`` вида ``<область>.<причина>`` (например
``game.desync``). Клиенты и тесты опираются на ``code``, а не на текст сообщения.
"""

from __future__ import annotations

from typing import Any, ClassVar


class AppError(Exception):
    """База всех ожидаемых ошибок приложения.

    Attributes:
        code: Машинный код ошибки, стабильный контракт для клиентов.
        message: Человекочитаемое описание (не показывать как есть, если содержит детали).
        details: Дополнительные безопасные для клиента данные.
    """

    default_code: ClassVar[str] = "app.error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.message = message
        self.details = details or {}


class DomainError(AppError):
    """Нарушение бизнес-правила (инварианта агрегата)."""

    default_code = "domain.rule_violated"


class InvalidInputError(AppError):
    """Входные данные корректны по формату, но недопустимы по смыслу."""

    default_code = "input.invalid"


class NotFoundError(AppError):
    """Ресурс не найден или недоступен текущему пользователю.

    Для чужих ресурсов используем именно её, а не ``ForbiddenError``: так не раскрывается
    сам факт существования ресурса (защита от перебора идентификаторов).
    """

    default_code = "resource.not_found"


class ConflictError(AppError):
    """Конфликт состояния: устаревшая версия, повтор, гонка."""

    default_code = "resource.conflict"


class UnauthenticatedError(AppError):
    """Нет валидных учётных данных."""

    default_code = "auth.unauthenticated"


class ForbiddenError(AppError):
    """Учётные данные есть, но прав недостаточно."""

    default_code = "auth.forbidden"


class RateLimitedError(AppError):
    """Превышен лимит частоты запросов."""

    default_code = "rate.limited"


class DependencyUnavailableError(AppError):
    """Внешняя зависимость временно недоступна; операцию можно повторить позже."""

    default_code = "dependency.unavailable"


class FeatureDisabledError(AppError):
    """Функция выключена фича-флагом или недоступна по плану пользователя."""

    default_code = "feature.disabled"
