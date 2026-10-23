"""Ручная выдача Версов и сторно (ADR 0043): зона повышенной безопасности.

Порядок в одной транзакции: ключ идемпотентности -> блокировка строки ``progress`` игрока
(сериализует выдачи и сторно одному игроку, суточный лимит не обойти параллелью) -> проверки ->
проводка через ``Poster`` (журнал, баланс, событие в outbox) -> аудит.
Сторнируются только кредиты журнала Версов; журнал опыта в 5a не сторнируется.
Причина (свободный текст) пишется только в аудит: ``source_ref`` проводки - id записи аудита.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from progression_service.application.accrual.common import published_config
from progression_service.application.accrual.posting import Poster
from progression_service.application.admin.common import (
    GRANT_REASON,
    REVERSAL_REASON,
    SOURCE_REF_PREFIX,
    AdminContext,
    AdminLimits,
    GrantLimitExceededError,
    LedgerEntryNotFoundError,
    NotReversibleLedgerError,
    UserNotFoundError,
    audit,
    decode_cursor,
    encode_cursor,
)
from progression_service.application.admin.metrics import ADMIN_OPERATIONS
from progression_service.application.admin.ports import AdminUnitOfWork, AdminUowFactory, Json
from progression_service.domain import ledger as domain
from progression_service.domain.ledger import Bucket, Direction, Entry, NotReversibleError

log = structlog.get_logger(__name__)


def entry_json(entry: Entry) -> Json:
    """Проводка для admin-API (``AdminLedgerEntry``)."""
    return {
        "op_id": entry.op_id,
        "direction": entry.direction.value,
        "amount": entry.amount,
        "bucket": entry.bucket.value,
        "reason": entry.reason,
        "source_ref": entry.source_ref,
        "reversal_of": entry.reversal_of,
        "actor_id": None if entry.actor_id is None else str(entry.actor_id),
        "config_version": entry.config_version,
        "at": entry.at.isoformat(),
    }


async def _operation(uow: AdminUnitOfWork, entry: Entry) -> Json:
    balance = (await uow.balances.all(entry.user_id)).get(entry.bucket, 0)
    return {
        "user_id": str(entry.user_id),
        "entry": entry_json(entry),
        "balance_after": balance,
    }


class LedgerCommands:
    """Мутации журнала Версов."""

    def __init__(self, uow: AdminUowFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def grant(self, user_id: UUID, *, amount: int, ctx: AdminContext) -> Json:
        """Выдать Версы вручную в бакет ``earned`` (``op_id = admin:{Idempotency-Key}``).

        Суточный лимит считает и сторнированные выдачи (защита от цикла «выдача -> сторно ->
        выдача», ADR 0043). Параллельный повтор того же ключа проигрывает как
        ``409 idempotency.key_reused``: ``op_id`` уже занят проводкой первого запроса.

        Raises:
            ReasonTooShortError: причина короче лимита.
            GrantLimitExceededError: выше лимита операции или суток.
            UserNotFoundError: у игрока нет прогресса.
        """
        fingerprint = request_fingerprint(
            "ledger.grant", user_id, {"amount": amount, "reason": ctx.reason}
        )

        async def perform(uow: AdminUnitOfWork) -> Json:
            published = await published_config(uow)
            limits = AdminLimits.of(published.config)
            limits.require_reason(ctx.reason)
            if amount > limits.grant_max_amount:
                _limit_exceeded(ctx, user_id, scope="operation")
                raise GrantLimitExceededError(
                    "сумма выдачи выше лимита операции",
                    limit=limits.grant_max_amount,
                    scope="operation",
                )
            if await uow.progress.get(user_id) is None:
                raise UserNotFoundError
            now = self._clock.now()
            poster = Poster(uow, published, now)
            await poster.lock_user(user_id)  # дальше выдачи этому игроку идут по очереди
            start, end = _day_bounds(now, published.config.tz)
            granted = await uow.admin_ledger.granted_between(user_id, start, end)
            if granted + amount > limits.grant_max_per_user_day:
                _limit_exceeded(ctx, user_id, scope="day")
                raise GrantLimitExceededError(
                    "сумма ручных выдач игроку за сутки выше лимита",
                    limit=limits.grant_max_per_user_day,
                    granted_today=granted,
                    scope="day",
                )
            action_id = uuid7()
            entry = domain.credit(
                op_id=f"admin:{ctx.idempotency_key}",
                user_id=user_id,
                amount=amount,
                reason=GRANT_REASON,
                source_ref=f"{SOURCE_REF_PREFIX}{action_id}",
                config_version=published.version,
                at=now,
                bucket=Bucket.EARNED,
                actor_id=ctx.actor_id,
            )
            await _post(poster, entry)
            await audit(
                uow,
                ctx,
                "ledger.grant",
                f"user:{user_id}",
                now,
                action_id=action_id,
                op_id=entry.op_id,
                amount=amount,
                bucket=entry.bucket.value,
            )
            return await _operation(uow, entry)

        response, executed = await self._run(fingerprint, ctx, perform)
        if executed:
            ADMIN_OPERATIONS.labels("ledger_granted", "ok").inc()
            log.info(
                "progression.admin.ledger_granted",
                actor_id=str(ctx.actor_id),
                user_id=str(user_id),
                op_id=response["entry"]["op_id"],
                amount=amount,
            )
        return response

    async def reverse(self, op_id: str, *, ctx: AdminContext) -> Json:
        """Сторнировать кредит Версов встречным дебетом с ``reversal_of``.

        Сторнируются только кредиты журнала Версов; дебеты, сторно и записи журнала опыта (в 5a
        опыт не сторнируется) - ``409 progression.not_reversible``.

        Raises:
            LedgerEntryNotFoundError: нет проводки.
            AlreadyReversedError: сторно уже есть.
            NotReversibleLedgerError: дебет, сторно или проводка опыта.
            InsufficientBalanceError: баланс ушёл бы ниже нуля.
        """
        fingerprint = request_fingerprint("ledger.reverse", op_id, {"reason": ctx.reason})

        async def perform(uow: AdminUnitOfWork) -> Json:
            published = await published_config(uow)
            AdminLimits.of(published.config).require_reason(ctx.reason)
            original = await uow.ledger.currency(op_id)
            if original is None:
                if await uow.ledger.has_xp(op_id):
                    raise NotReversibleLedgerError("опыт в 5a не сторнируется")
                raise LedgerEntryNotFoundError
            if original.direction is not Direction.CREDIT:
                raise NotReversibleLedgerError("сторнируются только начисления (кредиты)")
            now = self._clock.now()
            poster = Poster(uow, published, now)
            await poster.lock_user(original.user_id)  # проверка «уже сторнирована» - под замком
            action_id = uuid7()
            try:
                entry = domain.reversal(
                    original,
                    op_id=f"admin:{ctx.idempotency_key}",
                    actor_id=ctx.actor_id,
                    reason=REVERSAL_REASON,
                    at=now,
                    config_version=published.version,
                    already_reversed=await uow.ledger.is_reversed(op_id),
                )
            except NotReversibleError as exc:
                raise NotReversibleLedgerError(exc.message) from exc
            # Причина - только в аудите; op_id исходной проводки уже в `reversal_of`.
            entry = replace(entry, source_ref=f"{SOURCE_REF_PREFIX}{action_id}")
            await _post(poster, entry)
            await audit(
                uow,
                ctx,
                "ledger.reverse",
                f"user:{original.user_id}",
                now,
                action_id=action_id,
                op_id=entry.op_id,
                reversal_of=op_id,
                amount=entry.amount,
            )
            return await _operation(uow, entry)

        response, executed = await self._run(fingerprint, ctx, perform)
        if executed:
            ADMIN_OPERATIONS.labels("ledger_reversed", "ok").inc()
            log.info(
                "progression.admin.ledger_reversed",
                actor_id=str(ctx.actor_id),
                user_id=response["user_id"],
                op_id=response["entry"]["op_id"],
                reversal_of=op_id,
            )
        return response

    async def _run(
        self,
        fingerprint: str,
        ctx: AdminContext,
        perform: Callable[[AdminUnitOfWork], Awaitable[Json]],
    ) -> tuple[Json, bool]:
        async with self._uow() as uow:
            response, executed = await run_idempotent(
                uow.idempotency,
                ctx.actor_id,
                ctx.idempotency_key,
                fingerprint,
                lambda: perform(uow),
                status_code=201,
            )
            await uow.commit()
        return response, executed


def _limit_exceeded(ctx: AdminContext, user_id: UUID, *, scope: str) -> None:
    ADMIN_OPERATIONS.labels("ledger_granted", "limit_exceeded").inc()
    log.warning(
        "progression.admin.grant_limit_exceeded",
        actor_id=str(ctx.actor_id),
        user_id=str(user_id),
        scope=scope,
    )


async def _post(poster: Poster, entry: Entry) -> None:
    """Провести; занятый ``op_id`` = тот же ключ у другой операции."""
    if not await poster.post_currency(entry):
        raise ConflictError(
            "Idempotency-Key уже использован другой операцией", code="idempotency.key_reused"
        )


def _day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Границы суток ``now`` в зоне конфига, в UTC."""
    day = now.astimezone(tz).date()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


class LedgerQueries:
    """Чтение журнала игрока."""

    def __init__(self, uow: AdminUowFactory) -> None:
        self._uow = uow

    async def page(self, user_id: UUID, *, cursor: str | None, limit: int) -> Json:
        """Страница проводок и балансы в шапке.

        Raises:
            UserNotFoundError: у игрока нет прогресса.
        """
        before = None if cursor is None else decode_cursor(cursor)
        async with self._uow() as uow:
            if await uow.progress.get(user_id) is None:
                raise UserNotFoundError
            rows = await uow.admin_ledger.page(user_id, before=before, limit=limit + 1)
            balances = await uow.balances.all(user_id)
        more = rows[limit:]
        rows = rows[:limit]
        return {
            "user_id": str(user_id),
            "earned": balances.get(Bucket.EARNED, 0),
            "paid": balances.get(Bucket.PAID, 0),
            "items": [entry_json(r.entry) for r in rows],
            "next_cursor": encode_cursor(rows[-1].entry.at, rows[-1].id) if more else None,
        }
