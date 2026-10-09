"""Потребитель ``identity.user.v1``: синхронизация зеркала пользователей с identity.

``UserRolesChanged`` и ``UserDeleted`` применяются, только если ``version`` события больше
последней применённой (``identity_version``): старое или переставленное событие не откатит
права. Повтор доставки отсекает inbox (``ProcessedEvent``) в той же транзакции.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

import structlog
from asgiref.sync import sync_to_async
from django.db import connections, transaction
from pydantic import BaseModel, ValidationError

from admin_service.core.models import AdminUser, ProcessedEvent
from admin_service.core.permissions import PermissionSync
from gv_core.context import RequestContext, bind_context
from gv_messaging.consumer import DeadLetterSink, Outcome
from gv_messaging.envelope import EventEnvelope
from gv_messaging.metrics import EVENTS_CONSUMED
from gv_messaging.ports import PermanentError
from gv_messaging.retry import RetryPolicy

log = structlog.get_logger(__name__)

TOPICS = ("identity.user.v1",)
GROUP = "admin.consumer"


class _RolesChanged(BaseModel):
    user_id: UUID
    roles: list[str]
    perms: list[str]
    version: int


class _Deleted(BaseModel):
    user_id: UUID
    version: int


def _locked_newer(user_id: UUID, version: int) -> AdminUser | None:
    """Зеркало под блокировкой строки, если событие новее применённого; иначе ``None``.

    Пользователя, ещё ни разу не входившего в админку, не создаём: зеркало появляется при
    входе, и права тогда берутся из свежего токена.
    """
    user = AdminUser.objects.select_for_update().filter(identity_user_id=user_id).first()
    if user is None:
        return None
    if version <= user.identity_version:
        log.info("identity_event.stale", user_id=str(user_id), version=version)
        return None
    return user


def _on_roles_changed(payload: dict[str, Any]) -> None:
    event = _RolesChanged.model_validate(payload)
    user = _locked_newer(event.user_id, event.version)
    if user is None:
        return
    user.identity_version = event.version
    PermissionSync.apply(user, roles=event.roles, perms=event.perms)


def _on_deleted(payload: dict[str, Any]) -> None:
    event = _Deleted.model_validate(payload)
    user = _locked_newer(event.user_id, event.version)
    if user is None:
        return
    user.identity_version = event.version
    PermissionSync.revoke(user)


HANDLERS: dict[str, Callable[[dict[str, Any]], None]] = {
    "identity.UserRolesChanged": _on_roles_changed,
    "identity.UserDeleted": _on_deleted,
}


def handle_envelope(envelope: EventEnvelope, *, consumer: str = GROUP) -> Outcome:
    """Обработать событие в одной транзакции с отметкой в inbox (синхронно, Django ORM)."""
    handler = HANDLERS.get(envelope.event_type)
    if handler is None:
        return Outcome.SKIPPED
    # Вне HTTP-цикла Django сам не закрывает соединения: после рестарта БД обработчик
    # получил бы мёртвое соединение. Как request_started/finished у запросов.
    _drop_stale_connections()
    try:
        return _handle(envelope, handler, consumer)
    finally:
        _drop_stale_connections()


def _drop_stale_connections() -> None:
    for conn in connections.all(initialized_only=True):
        if not conn.in_atomic_block:  # внешняя транзакция (тесты) — не наша
            conn.close_if_unusable_or_obsolete()


def _handle(
    envelope: EventEnvelope, handler: Callable[[dict[str, Any]], None], consumer: str
) -> Outcome:
    ctx = RequestContext(
        correlation_id=envelope.correlation_id or envelope.event_id,
        causation_id=envelope.event_id,
    )
    with bind_context(ctx), transaction.atomic():
        _, created = ProcessedEvent.objects.get_or_create(
            consumer=consumer, event_id=envelope.event_id
        )
        if not created:
            return Outcome.DUPLICATE
        try:
            handler(envelope.payload)
        except ValidationError as exc:
            # Payload не по контракту — ретраи не помогут.
            raise PermanentError(str(exc)[:500]) from exc
    return Outcome.PROCESSED


class UserEventsProcessor:
    """Разбор, ретраи и DLQ поверх ``handle_envelope`` (та же семантика, что у
    ``gv_messaging.consumer.EventProcessor``, но транзакция и inbox — в Django ORM)."""

    def __init__(
        self,
        *,
        dead_letters: DeadLetterSink,
        group: str = GROUP,
        retry: RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._dlq = dead_letters
        self._group = group
        self._retry = retry or RetryPolicy()
        self._sleep = sleep

    async def process(self, raw: bytes, *, source: str) -> Outcome:
        """Обработать сообщение; возврат — offset можно коммитить."""
        try:
            envelope = EventEnvelope.from_bytes(raw)
        except ValidationError as exc:
            await self._dlq.send(raw, reason="malformed", error=str(exc)[:2000], source=source)
            EVENTS_CONSUMED.labels(self._group, "unknown", Outcome.DEAD).inc()
            log.error("event.malformed", source=source)  # noqa: TRY400 — трейсбек не нужен
            return Outcome.DEAD
        handle = sync_to_async(handle_envelope, thread_sensitive=True)
        attempt = 0
        while True:
            attempt += 1
            try:
                outcome = await handle(envelope, consumer=self._group)
            except PermanentError as exc:
                return await self._dead(raw, envelope, source, "permanent", exc)
            except Exception as exc:
                log.warning(
                    "event.handler_failed",
                    event_id=str(envelope.event_id),
                    event_type=envelope.event_type,
                    attempt=attempt,
                    exc_info=True,
                )
                if not self._retry.should_retry(attempt):
                    return await self._dead(raw, envelope, source, "retries_exhausted", exc)
                await self._sleep(self._retry.delay(attempt))
                continue
            EVENTS_CONSUMED.labels(self._group, envelope.event_type, outcome).inc()
            return outcome

    async def _dead(
        self, raw: bytes, envelope: EventEnvelope, source: str, reason: str, exc: Exception
    ) -> Outcome:
        await self._dlq.send(raw, reason=reason, error=repr(exc)[:2000], source=source)
        EVENTS_CONSUMED.labels(self._group, envelope.event_type, Outcome.DEAD).inc()
        log.error("event.dead_lettered", event_id=str(envelope.event_id), reason=reason)
        return Outcome.DEAD
