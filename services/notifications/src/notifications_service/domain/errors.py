"""Ошибки notifications: машинные коды ``notifications.*``."""

from __future__ import annotations

from gv_core.errors import InvalidInputError, NotFoundError


class ConfigError(Exception):
    """Конфиг типов уведомлений не прошёл проверку: сервис не стартует (ADR 0047)."""


class MappingError(Exception):
    """Событие не соответствует контракту, по которому настроен тип (поле нет или неверного вида).

    Повтором не лечится: потребитель отправляет событие в DLQ (``PermanentError``).
    """


class NotificationNotFoundError(NotFoundError):
    """Уведомления нет или оно чужое (владение не раскрываем)."""

    default_code = "notifications.not_found"

    def __init__(self) -> None:
        super().__init__("уведомление не найдено")


class TypeNotFoundError(NotFoundError):
    """Типа нет в конфиге."""

    default_code = "notifications.type_not_found"

    def __init__(self) -> None:
        super().__init__("тип уведомления не найден")


class TypeNotMutableError(InvalidInputError):
    """Тип с ``user_mutable = false`` отключить нельзя."""

    default_code = "notifications.type_not_mutable"

    def __init__(self) -> None:
        super().__init__("этот тип уведомлений нельзя отключить")


class TypeNotSwitchableError(InvalidInputError):
    """Системный тип (``admin_switchable = false``) админ выключить не может."""

    default_code = "notifications.type_not_switchable"

    def __init__(self) -> None:
        super().__init__("этот тип уведомлений нельзя выключить")


class DeviceNotFoundError(NotFoundError):
    """Устройства нет или оно чужое."""

    default_code = "notifications.device_not_found"

    def __init__(self) -> None:
        super().__init__("устройство не найдено")


class UnsubscribeInvalidError(InvalidInputError):
    """Токен отписки неверный, чужой или повреждённый (все случаи неразличимы)."""

    default_code = "notifications.unsubscribe_invalid"

    def __init__(self) -> None:
        super().__init__("ссылка отписки недействительна")
