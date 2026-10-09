"""Общие кирпичи мутирующих (в первую очередь admin) ручек FastAPI.

* ``IdempotencyKey`` — обязательный заголовок ``Idempotency-Key`` (CLAUDE.md, правило 5);
* ``Reason`` — поле ``reason`` с причиной действия для аудита.

Хранение ключей и ответов — ``gv_db.idempotency``.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, Header
from pydantic import StringConstraints

from gv_core.errors import InvalidInputError

IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:-]{8,128}$"
_KEY_RE = re.compile(IDEMPOTENCY_KEY_PATTERN)

REASON_MIN_LENGTH = 3
REASON_MAX_LENGTH = 500

Reason = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=REASON_MIN_LENGTH, max_length=REASON_MAX_LENGTH
    ),
]
"""Причина действия: пробелы по краям срезаются, затем длина 3..500."""


def idempotency_key(
    key: Annotated[
        str | None,
        Header(
            alias=IDEMPOTENCY_HEADER,
            description=(
                "Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. "
                "Повтор с тем же ключом и телом возвращает сохранённый ответ, "
                "с другим телом — 409 idempotency.key_reused."
            ),
        ),
    ] = None,
) -> str:
    """Проверить заголовок ``Idempotency-Key``.

    В схеме заголовок необязателен намеренно: иначе FastAPI отвечал бы на его отсутствие
    общей ``request.validation_failed``, а клиентам нужен точный код.

    Raises:
        InvalidInputError: ``idempotency.key_required`` — заголовка нет;
            ``idempotency.key_invalid`` — неверный формат.
    """
    if key is None:
        raise InvalidInputError(
            f"нужен заголовок {IDEMPOTENCY_HEADER}", code="idempotency.key_required"
        )
    if not _KEY_RE.fullmatch(key):
        raise InvalidInputError(
            f"неверный {IDEMPOTENCY_HEADER}: 8–128 символов [A-Za-z0-9_.:-]",
            code="idempotency.key_invalid",
        )
    return key


IdempotencyKey = Annotated[str, Depends(idempotency_key)]
"""Параметр ручки: ``key: IdempotencyKey`` — проверенный ``Idempotency-Key``."""
