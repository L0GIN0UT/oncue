"""Идемпотентные мутации по ``(actor_id, Idempotency-Key)``.

Ключ и сохранённый ответ записываются **в той же транзакции**, что и сама мутация. Поэтому:

* ошибка или откат мутации не оставляет «залипшего» ключа — повтор выполнится заново;
* успешный коммит фиксирует и изменение, и ответ — повтор получит ровно его;
* гонку двух запросов с одинаковым ключом решает ``PRIMARY KEY (actor_id, key)``: второй
  ждёт блокировку строки первого и получает ``ConflictError(idempotency.in_progress)``,
  а его транзакция откатывается целиком.

Подключение в сервисе::

    # infrastructure/db/tables.py
    IDEMPOTENCY = idempotency_table(TABLES.metadata)
    # + миграция Alembic, создающая таблицу idempotency_keys

    # use-case (внутри открытого Unit of Work)
    store = SqlIdempotencyStore(uow.session, IDEMPOTENCY, clock)
    response, executed = await run_idempotent(store, actor_id, key, fingerprint, perform)
    await uow.commit()

Старые ключи удаляются периодической задачей через ``purge_older_than``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    Uuid,
    delete,
    insert,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from gv_core.clock import Clock
from gv_core.errors import ConflictError

IDEMPOTENCY_TABLE = "idempotency_keys"
KEY_MAX_LENGTH = 128

# JSON-ответ мутации (объект верхнего уровня).
type JsonObject = dict[str, Any]


def idempotency_table(metadata: MetaData, name: str = IDEMPOTENCY_TABLE) -> Table:
    """Объявить таблицу ключей идемпотентности в метаданных сервиса.

    Повторный вызов с теми же метаданными и именем возвращает уже объявленную таблицу.

    Args:
        metadata: Метаданные сервиса (``create_metadata().metadata``).
        name: Имя таблицы; другое имя — если в сервисе нужно несколько областей ключей.
    """
    if name in metadata.tables:
        return metadata.tables[name]
    return Table(
        name,
        metadata,
        Column("actor_id", Uuid(as_uuid=True), nullable=False),
        Column("key", String(KEY_MAX_LENGTH), nullable=False),
        Column("request_hash", String(64), nullable=False),
        Column("response", JSONB, nullable=False),
        Column("status_code", SmallInteger, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        # PK = UNIQUE(actor_id, key): на нём и держится защита от гонки.
        PrimaryKeyConstraint("actor_id", "key"),
        CheckConstraint("status_code BETWEEN 200 AND 299", name="status_code_success"),
        CheckConstraint("length(request_hash) = 64", name="request_hash_sha256"),
        Index(f"ix_{name}_created_at", "created_at"),
    )


def request_fingerprint(action: str, target: object, body: Mapping[str, Any]) -> str:
    """Отпечаток запроса (sha256 hex): тот же ключ с другим запросом — ошибка клиента.

    Алгоритм совпадает с ``admin_actions.request_hash`` catalog — переезд сервиса на эту
    библиотеку не инвалидирует уже сохранённые ключи.

    Args:
        action: Имя операции, например ``item.block``.
        target: Цель операции (обычно UUID); ``None`` — для операций без цели.
        body: Значимые поля запроса (включая ``reason``).
    """
    raw = json.dumps(
        {"action": action, "target": str(target), "body": dict(body)},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """Сохранённый результат мутации."""

    actor_id: uuid.UUID
    key: str
    request_hash: str
    response: JsonObject
    status_code: int
    created_at: datetime


class IdempotencyStore(Protocol):
    """Порт хранилища ключей. Реализация работает в транзакции вызывающего."""

    async def find(self, actor_id: uuid.UUID, key: str) -> IdempotencyRecord | None:
        """Ранее сохранённый результат или ``None``."""
        ...

    async def save(
        self,
        actor_id: uuid.UUID,
        key: str,
        request_hash: str,
        response: JsonObject,
        *,
        status_code: int = 200,
    ) -> None:
        """Сохранить результат.

        Raises:
            ConflictError: ``idempotency.in_progress`` — ключ уже занят параллельным
                запросом.
        """
        ...


class SqlIdempotencyStore:
    """``IdempotencyStore`` на PostgreSQL поверх сессии или соединения текущей транзакции.

    Не коммитит и не открывает транзакций сам: границы задаёт Unit of Work вызывающего.
    """

    def __init__(self, conn: AsyncSession | AsyncConnection, table: Table, clock: Clock) -> None:
        self._conn = conn
        self._table = table
        self._clock = clock

    async def find(self, actor_id: uuid.UUID, key: str) -> IdempotencyRecord | None:
        """Ранее сохранённый результат или ``None``."""
        t = self._table
        row = (
            await self._conn.execute(select(t).where(t.c.actor_id == actor_id, t.c.key == key))
        ).first()
        if row is None:
            return None
        return IdempotencyRecord(
            actor_id=row.actor_id,
            key=row.key,
            request_hash=row.request_hash,
            response=dict(row.response),
            status_code=row.status_code,
            created_at=row.created_at,
        )

    async def save(
        self,
        actor_id: uuid.UUID,
        key: str,
        request_hash: str,
        response: JsonObject,
        *,
        status_code: int = 200,
    ) -> None:
        """Сохранить результат в текущей транзакции.

        Вставка идёт в savepoint: при конфликте откатывается только она, а решение об
        откате всей транзакции остаётся за вызывающим (исключение он всё равно получит).

        Raises:
            ConflictError: ``idempotency.in_progress`` — тот же ключ уже записан
                параллельной транзакцией.
        """
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    insert(self._table).values(
                        actor_id=actor_id,
                        key=key,
                        request_hash=request_hash,
                        response=response,
                        status_code=status_code,
                        created_at=self._clock.now(),
                    )
                )
        except IntegrityError as exc:
            raise ConflictError(
                "запрос с этим Idempotency-Key уже выполняется", code="idempotency.in_progress"
            ) from exc

    async def purge_older_than(self, cutoff: datetime) -> int:
        """Удалить ключи, созданные раньше ``cutoff``.

        Returns:
            Число удалённых записей.
        """
        result = await self._conn.execute(
            delete(self._table).where(self._table.c.created_at < cutoff)
        )
        return int(getattr(result, "rowcount", 0) or 0)


async def run_idempotent(
    store: IdempotencyStore,
    actor_id: uuid.UUID,
    key: str,
    fingerprint: str,
    perform: Callable[[], Awaitable[JsonObject]],
    *,
    status_code: int = 200,
) -> tuple[JsonObject, bool]:
    """Выполнить ``perform`` не больше одного раза на ``(actor_id, key)``.

    Вызывается внутри транзакции мутации; ``store`` должен работать в ней же. Коммит —
    забота вызывающего: без него не сохранится ни мутация, ни ключ.

    Args:
        store: Хранилище ключей текущей транзакции.
        actor_id: Кто выполняет мутацию (ключи разных акторов независимы).
        key: Значение ``Idempotency-Key``.
        fingerprint: ``request_fingerprint(...)`` запроса.
        perform: Сама мутация; возвращает JSON-объект ответа.
        status_code: Успешный статус ответа, сохраняется вместе с ним.

    Returns:
        Ответ и признак «выполнено сейчас» (``False`` — повтор, ответ из хранилища).
        Ответ нормализован через JSON — первый вызов и повтор возвращают одно и то же.

    Raises:
        ConflictError: ``idempotency.key_reused`` — ключ уже использован с другим запросом;
            ``idempotency.in_progress`` — параллельный запрос с тем же ключом.
        TypeError: ответ ``perform`` не сериализуется в JSON.
    """
    existing = await store.find(actor_id, key)
    if existing is not None:
        if existing.request_hash != fingerprint:
            raise ConflictError(
                "Idempotency-Key уже использован с другим запросом", code="idempotency.key_reused"
            )
        return existing.response, False
    # Строгая сериализация: UUID/datetime в ответе — ошибка вызывающего, а не тихий str().
    response: JsonObject = json.loads(json.dumps(await perform(), ensure_ascii=False))
    await store.save(actor_id, key, fingerprint, response, status_code=status_code)
    return response, True
