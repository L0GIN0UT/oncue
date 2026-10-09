"""Идемпотентность admin-мутаций по ``Idempotency-Key``.

Ключ резервируется отдельной транзакцией ДО мутации: параллельный повтор с тем же ключом
получает 409 ``idempotency.in_progress``, а не второе выполнение. После успеха ответ
сохраняется и возвращается повторам; после ошибки ключ освобождается.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from gv_core.clock import Clock
from gv_core.errors import ConflictError, InvalidInputError
from media_pipeline_service.application.ports import UnitOfWorkFactory

_KEY_RE = re.compile(r"^[A-Za-z0-9_\-:.]{8,128}$")

Response = tuple[int, dict[str, Any]]


def validate_key(key: str | None) -> str:
    """Проверить формат ключа (8–128 символов ``[A-Za-z0-9_-:.]``)."""
    if key is None or not _KEY_RE.match(key):
        raise InvalidInputError(
            "нужен заголовок Idempotency-Key (8–128 символов: латиница, цифры, _-:.)",
            code="idempotency.key_required",
        )
    return key


class IdempotentExecutor:
    """Выполнить мутацию не больше одного раза на ``(scope, key)``."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def run(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        action: Callable[[], Awaitable[Response]],
    ) -> Response:
        """Выполнить ``action`` или вернуть сохранённый ответ.

        Raises:
            ConflictError: ключ уже использован с другим запросом
                (``idempotency.key_reused``) или запрос с ним ещё выполняется
                (``idempotency.in_progress``).
        """
        async with self._uow() as uow:
            existing = await uow.idempotency.reserve(scope, key, request_hash, self._clock.now())
            await uow.commit()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ConflictError(
                    "Idempotency-Key уже использован с другим запросом",
                    code="idempotency.key_reused",
                )
            if not existing.completed or existing.response is None:
                raise ConflictError(
                    "запрос с этим Idempotency-Key ещё выполняется", code="idempotency.in_progress"
                )
            return existing.status_code or 200, existing.response
        try:
            status, body = await action()
        except BaseException:
            async with self._uow() as uow:
                await uow.idempotency.release(scope, key)
                await uow.commit()
            raise
        async with self._uow() as uow:
            await uow.idempotency.complete(scope, key, status, body)
            await uow.commit()
        return status, body
