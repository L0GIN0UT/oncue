"""Информационные рассылки администрации: постановка и отправка (ADR 0047).

Письмо уходит только подписавшимся (согласие), в письме - ссылка отписки с подписанным токеном.
Воркер идемпотентен: перед отправкой занимает ``(broadcast_id, user_id)`` в
``broadcast_deliveries`` (UNIQUE), поэтому повтор задачи не шлёт письмо дважды; при сбое
отправки занятие снимается и повтор дошлёт письмо.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from prometheus_client import Counter

from gv_core.clock import Clock
from gv_core.errors import DependencyUnavailableError
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_messaging.ports import PermanentError, TaskQueue
from notifications_service.application.cursor import decode_cursor, encode_cursor
from notifications_service.application.ports import (
    PERMANENT_DIRECTORY_CODES,
    AdminAction,
    Broadcast,
    EmailDirectory,
    EmailMessage,
    EmailSender,
    PermanentEmailError,
    UnitOfWorkFactory,
)
from notifications_service.domain.tokens import UnsubscribeTokens

log = structlog.get_logger(__name__)

BROADCAST_TASK = "notifications.broadcast.send"
BROADCAST_EMAILS = Counter(
    "gv_notifications_broadcast_emails_total", "Письма рассылок", ["result"]
)  # result: sent | failed


class BroadcastNotVisibleError(Exception):
    """Рассылка ещё не видна (задача обогнала коммит): очередь повторит позже."""


@dataclass(frozen=True, slots=True)
class BroadcastPageResult:
    """Страница рассылок."""

    items: list[Broadcast]
    next_cursor: str | None


def broadcast_json(b: Broadcast) -> dict[str, Any]:  # Any: JSON-ответ идемпотентной мутации
    """Ответ создания рассылки (хранится в ключе идемпотентности)."""
    return {
        "id": str(b.id),
        "subject": b.subject,
        "status": b.status,
        "recipients": b.recipients,
        "created_at": b.created_at.isoformat(),
        "created_by": str(b.created_by),
    }


class CreateBroadcast:
    """Ставит рассылку: запись со снимком числа получателей и задача RabbitMQ."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        tasks: TaskQueue,
        clock: Clock,
        new_id: Callable[[], UUID],
    ) -> None:
        self._uow = uow_factory
        self._tasks = tasks
        self._clock = clock
        self._new_id = new_id

    async def execute(
        self, *, actor_id: UUID, key: str, subject: str, body: str, reason: str
    ) -> tuple[dict[str, Any], bool]:
        """Создать рассылку (идемпотентно по ``Idempotency-Key``).

        Задача ставится до коммита: если постановка упала, запись откатывается и повтор с тем же
        ключом выполнится заново; если коммит упал после постановки, воркер не найдёт рассылку и
        отбросит задачу после повторов.

        Returns:
            Ответ и признак «выполнено сейчас».
        """
        fingerprint = request_fingerprint(
            "broadcast.create", None, {"subject": subject, "body": body, "reason": reason}
        )
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                now = self._clock.now()
                broadcast = Broadcast(
                    id=self._new_id(),
                    subject=subject,
                    body=body,
                    status="queued",
                    recipients=await uow.subscriptions.count_subscribed(),
                    delivered=0,
                    failed=0,
                    created_at=now,
                    created_by=actor_id,
                )
                await uow.broadcasts.add(broadcast)
                await uow.audit.record(
                    AdminAction(
                        id=self._new_id(),
                        actor_id=actor_id,
                        action="broadcast.create",
                        target=str(broadcast.id),
                        reason=reason,
                        at=now,
                        details={"recipients": broadcast.recipients},
                    )
                )
                await self._tasks.enqueue(
                    BROADCAST_TASK, {"broadcast_id": str(broadcast.id)}, task_id=str(broadcast.id)
                )
                return broadcast_json(broadcast)

            response, executed = await run_idempotent(
                uow.idempotency, actor_id, key, fingerprint, perform, status_code=202
            )
            await uow.commit()
        return response, executed


class BroadcastQueries:
    """Чтение рассылок для админки."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow = uow_factory

    async def page(self, *, cursor: str | None, limit: int) -> BroadcastPageResult:
        """Новые первыми."""
        after = decode_cursor(cursor) if cursor is not None else None
        async with self._uow() as uow:
            rows = await uow.broadcasts.page(after=after, limit=limit + 1)
        items = rows[:limit]
        more = len(rows) > limit
        next_cursor = encode_cursor(items[-1].created_at, items[-1].id) if more else None
        return BroadcastPageResult(items=items, next_cursor=next_cursor)


class SendBroadcast:
    """Воркер рассылки: порциями, только подписавшимся, без повторной отправки."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        directory: EmailDirectory,
        sender: EmailSender,
        tokens: UnsubscribeTokens,
        clock: Clock,
        *,
        public_base_url: str,
        footer: str,
        batch_size: int,
    ) -> None:
        self._uow = uow_factory
        self._directory = directory
        self._sender = sender
        self._tokens = tokens
        self._clock = clock
        self._base = public_base_url.rstrip("/")
        self._footer = footer
        self._batch = batch_size

    def _message(self, broadcast: Broadcast, user_id: UUID, to: str) -> EmailMessage:
        link = f"{self._base}/unsubscribe?token={self._tokens.issue(user_id, broadcast.id)}"
        text = f"{broadcast.body}\n\n{self._footer.replace('{link}', link)}"
        return EmailMessage(
            to=to,
            subject=broadcast.subject,
            body=text,
            # RFC 8058: однократная отписка без перехода по ссылке; mailto не нужен.
            headers={
                "List-Unsubscribe": f"<{link}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        )

    async def execute(self, broadcast_id: UUID) -> None:
        """Отправить рассылку (повтор безопасен).

        Гарантии: письмо одному получателю уходит не больше одного раза. Перед отправкой
        воркер занимает ``(broadcast_id, user_id)``; падение процесса между занятием и ``send``
        теряет это одно письмо, но не дублирует его (допустимо для информационной рассылки).
        Временный сбой отправки снимает занятие, задача повторяется и дошлёт письмо.
        Окончательный отказ адреса (``PermanentEmailError``) фиксируется как ``failed``-доставка
        и не повторяется. Рассылка завершается статусом ``sent`` (есть ушедшие письма или
        некому слать) либо ``failed`` (не ушло ни одного письма при отказах).

        Raises:
            BroadcastNotVisibleError: рассылки ещё нет (повтор позже).
            PermanentError: адреса получателей недоступны по конфигурации (рассылка ``failed``).
            RuntimeError: часть писем не ушла временно - задача будет повторена.
        """
        async with self._uow() as uow:
            broadcast = await uow.broadcasts.get(broadcast_id, lock=True)
            if broadcast is None:
                raise BroadcastNotVisibleError(str(broadcast_id))
            if broadcast.status in {"sent", "failed"}:
                return
            await uow.broadcasts.set_status(broadcast_id, "sending")
            await uow.commit()

        failures = 0
        cursor: UUID | None = None
        while True:
            async with self._uow() as uow:
                user_ids = await uow.subscriptions.undelivered_after(
                    broadcast_id, cursor, self._batch
                )
            if not user_ids:
                break
            cursor = user_ids[-1]
            try:
                recipients = await self._directory.resolve(user_ids)
            except DependencyUnavailableError as exc:
                if exc.code in PERMANENT_DIRECTORY_CODES:
                    await self._fail(broadcast_id)
                    raise PermanentError(exc.message) from exc
                raise
            failures += await self._send_batch(broadcast, recipients)

        if failures:
            raise RuntimeError(f"рассылка {broadcast_id}: не отправлено писем: {failures}")
        async with self._uow() as uow:
            done = await uow.broadcasts.get(broadcast_id)
            nothing_went = done is not None and done.delivered == 0 and done.failed > 0
            status = "failed" if nothing_went else "sent"
            await uow.broadcasts.set_status(broadcast_id, status)
            await uow.commit()
        log.info("notifications.broadcast_done", broadcast_id=str(broadcast_id), status=status)

    async def _fail(self, broadcast_id: UUID) -> None:
        async with self._uow() as uow:
            await uow.broadcasts.set_status(broadcast_id, "failed")
            await uow.commit()

    async def _send_batch(self, broadcast: Broadcast, recipients: list[Any]) -> int:
        sent = failures = refused = 0
        for recipient in recipients:
            async with self._uow() as uow:
                # Занятие и проверка согласия - одним запросом: отписавшийся с начала порции
                # письма уже не получит, повтор воркера второй раз не отправит.
                claimed = await uow.broadcasts.claim(
                    broadcast.id, recipient.user_id, self._clock.now()
                )
                await uow.commit()
            if not claimed:
                continue
            try:
                await self._sender.send(
                    self._message(broadcast, recipient.user_id, recipient.email)
                )
            except PermanentEmailError:
                # Адрес отвергнут окончательно: доставка остаётся в таблице как failed, повтора нет.
                log.warning("notifications.broadcast_email_refused")
                BROADCAST_EMAILS.labels("refused").inc()
                refused += 1
                async with self._uow() as uow:
                    await uow.broadcasts.mark_failed(broadcast.id, recipient.user_id)
                    await uow.commit()
                continue
            except Exception:
                # Адрес не логируем (ПДн); письмо будет дослано повтором задачи.
                log.warning("notifications.broadcast_email_failed", exc_info=False)
                BROADCAST_EMAILS.labels("failed").inc()
                failures += 1
                async with self._uow() as uow:
                    await uow.broadcasts.release(broadcast.id, recipient.user_id)
                    await uow.commit()
                continue
            BROADCAST_EMAILS.labels("sent").inc()
            sent += 1
        if sent or refused:
            async with self._uow() as uow:
                await uow.broadcasts.add_counts(broadcast.id, delivered=sent, failed=refused)
                await uow.commit()
        return failures
