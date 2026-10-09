"""Ошибки генерации (внутренние для задач и адаптеров, не ответы API)."""

from __future__ import annotations


class GenerationError(Exception):
    """Сбой генерации или загрузки исходника.

    Attributes:
        code: Машинный код (попадает в ``AssetsFailed.error_code``), до 64 символов.
        retryable: Имеет ли смысл повтор (сеть, хранилище) или вход битый навсегда.
    """

    def __init__(self, code: str, message: str = "", *, retryable: bool) -> None:
        super().__init__(message or code)
        self.code = code[:64]
        self.retryable = retryable


class GenerationRetryError(Exception):
    """Попытка не удалась, но будет повтор: задача должна вернуться в очередь RabbitMQ."""

    def __init__(self, code: str) -> None:
        super().__init__(f"генерация будет повторена: {code}")
        self.code = code
