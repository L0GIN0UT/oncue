"""Санкции: выдача, снятие, истечение, чтение (ADR 0052). Реализует порты ``SanctionGranter`` и
``SanctionReader`` (шов с потоком F4).

Каждое изменение набора санкций игрока в одной транзакции: блокировка и ``version`` игрока,
правка записей, событие ``UserSanctionsChanged`` в outbox (полное действующее состояние).
Одна не снятая запись на ``(игрок, вид)``: повторная выдача того же вида закрывает прежнюю
(``replaced``) и открывает новую со свежим сроком.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import ForbiddenError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from social_service.application.config_access import current_config
from social_service.application.ports.config import ConfigProvider
from social_service.application.ports.sanctions import GrantRequest, SanctionKind, SanctionRecord
from social_service.application.ports.sanctions_store import (
    SanctionsRepository,
    SanctionsUnitOfWork,
    SanctionsUowFactory,
    UserCardData,
)
from social_service.domain import sanctions as rules
from social_service.domain.config import SanctionsConfig
from social_service.domain.sanctions import ActiveSanction, UserSanctionsChanged

EXPIRE_BATCH = 200
HISTORY_LIMIT = 100
CASES_LIMIT = 50


def record_to_json(record: SanctionRecord) -> dict[str, Any]:
    """Запись -> JSON-объект (ответ идемпотентной выдачи)."""

    def ts(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": str(record.id),
        "user_id": str(record.user_id),
        "kind": record.kind,
        "started_at": ts(record.started_at),
        "expires_at": ts(record.expires_at),
        "reason": record.reason,
        "actor_id": str(record.actor_id),
        "revoked_at": ts(record.revoked_at),
        "revoked_by": str(record.revoked_by) if record.revoked_by else None,
        "revoke_reason": record.revoke_reason,
    }


def record_from_json(data: dict[str, Any]) -> SanctionRecord:
    """JSON-объект -> запись."""

    def ts(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value is not None else None

    started = ts(data["started_at"])
    assert started is not None  # noqa: S101 - поле обязательно в сохранённом ответе
    kind: SanctionKind = data["kind"]
    return SanctionRecord(
        id=UUID(data["id"]),
        user_id=UUID(data["user_id"]),
        kind=kind,
        started_at=started,
        expires_at=ts(data["expires_at"]),
        reason=data["reason"],
        actor_id=UUID(data["actor_id"]),
        revoked_at=ts(data["revoked_at"]),
        revoked_by=UUID(data["revoked_by"]) if data["revoked_by"] else None,
        revoke_reason=data["revoke_reason"],
    )


class SanctionService:
    """Use-cases санкций."""

    def __init__(
        self,
        uow_factory: SanctionsUowFactory,
        config: ConfigProvider,
        clock: Clock,
        new_id: Callable[[], UUID] = uuid7,
    ) -> None:
        self._uow = uow_factory
        self._config = config
        self._clock = clock
        self._new_id = new_id

    # ------------------------------------------------------------------ чтение
    async def active_mask(self, user_id: UUID) -> int:
        """Маска действующих санкций (как claim ``sanctions``; ``account`` не входит)."""
        async with self._uow() as uow:
            active = await uow.sanctions.active(user_id, self._clock.now())
        return rules.mask_of(r.kind for r in active)

    async def my_active(self, user_id: UUID) -> list[SanctionRecord]:
        """Действующие санкции игрока (экран санкции)."""
        async with self._uow() as uow:
            return await uow.sanctions.active(user_id, self._clock.now())

    async def history(self, user_id: UUID) -> list[SanctionRecord]:
        """История санкций игрока для модератора, новые первыми."""
        async with self._uow() as uow:
            return await uow.sanctions.history(user_id, HISTORY_LIMIT)

    async def moderation_card(
        self, user_id: UUID
    ) -> tuple[UserCardData, list[SanctionRecord], datetime]:
        """Карточка игрока для модератора: данные, история санкций и «сейчас».

        Raises:
            NotFoundError: ``social.player_not_found`` - игрока нет ни в проекции, ни в истории.
        """
        async with self._uow() as uow:
            repo = uow.sanctions
            history = await repo.history(user_id, HISTORY_LIMIT)
            card = await repo.user_card(user_id, CASES_LIMIT)
            known = bool(history) or card.nickname is not None or bool(card.counters)
            if not known and not await repo.player_exists(user_id):
                raise NotFoundError("игрок не найден", code=rules.PLAYER_NOT_FOUND)
        return card, history, self._clock.now()

    # ------------------------------------------------------------------ выдача
    async def grant(self, request: GrantRequest) -> SanctionRecord:
        """Выдать санкцию в текущей транзакции (``SanctionGranter``)."""
        cfg = await current_config(self._config)
        async with self._uow() as uow:
            record = await self._grant(uow, request, cfg.sanctions)
            await uow.commit()
        return record

    async def grant_idempotent(
        self, request: GrantRequest, key: str, *, check_player: bool = True
    ) -> tuple[SanctionRecord, bool]:
        """Выдача из админ-API: ``Idempotency-Key`` по модератору; ``(запись, выполнено сейчас)``.

        Raises:
            NotFoundError: ``social.player_not_found`` - игрока нет в проекции.
        """
        cfg = await current_config(self._config)
        fingerprint = request_fingerprint(
            "sanction.grant",
            request.user_id,
            {"kind": request.kind, "duration_s": request.duration_s, "reason": request.reason},
        )
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                record = await self._grant(uow, request, cfg.sanctions, check_player=check_player)
                return record_to_json(record)

            data, executed = await run_idempotent(
                uow.idempotency, request.actor_id, key, fingerprint, perform, status_code=201
            )
            await uow.commit()
        return record_from_json(data), executed

    async def _grant(
        self,
        uow: SanctionsUnitOfWork,
        request: GrantRequest,
        limits: SanctionsConfig,
        *,
        check_player: bool = False,
    ) -> SanctionRecord:
        if request.actor_id == request.user_id:
            raise ForbiddenError("себе санкции не выдают", code=rules.SELF_SANCTION_FORBIDDEN)
        if request.kind == "account" and rules.ACCOUNT_PERM not in request.actor_perms:
            raise ForbiddenError(
                "бан аккаунта требует отдельного права", code=rules.ACCOUNT_SANCTION_FORBIDDEN
            )
        if not rules.duration_in_bounds(
            request.duration_s, min_s=limits.min_duration_s, max_s=limits.max_duration_s
        ):
            raise InvalidInputError(
                "срок вне допустимых пределов",
                code=rules.DURATION_INVALID,
                details={"min_s": limits.min_duration_s, "max_s": limits.max_duration_s},
            )
        repo = uow.sanctions
        if check_player and not await repo.player_exists(request.user_id):
            raise NotFoundError("игрок не найден", code=rules.PLAYER_NOT_FOUND)
        now = self._clock.now()
        version = await repo.lock_and_bump(request.user_id)
        previous = await repo.open_of_kind(request.user_id, request.kind)
        if previous is not None:
            await self._close_previous(repo, previous, request.actor_id, now)
        record = SanctionRecord(
            id=self._new_id(),
            user_id=request.user_id,
            kind=request.kind,
            started_at=now,
            expires_at=(
                now + timedelta(seconds=request.duration_s)
                if request.duration_s is not None
                else None
            ),
            reason=request.reason,
            actor_id=request.actor_id,
        )
        await repo.insert(record)
        await self._emit(uow, request.user_id, version, request.actor_id, now)
        return record

    @staticmethod
    async def _close_previous(
        repo: SanctionsRepository, previous: SanctionRecord, actor_id: UUID, now: datetime
    ) -> None:
        if previous.expires_at is not None and previous.expires_at <= now:
            # Срок вышел, задача истечения ещё не успела: это истечение, а не замена.
            await repo.close(previous.id, at=now, by=None, reason=rules.REVOKE_EXPIRED)
        else:
            await repo.close(previous.id, at=now, by=actor_id, reason=rules.REVOKE_REPLACED)

    # ------------------------------------------------------------------ снятие
    async def revoke(
        self,
        actor_id: UUID,
        sanction_id: UUID,
        reason: str,
        actor_perms: frozenset[str] = frozenset(),
    ) -> SanctionRecord:
        """Снять санкцию вручную; повтор возвращает ту же (уже снятую) запись без события.

        Raises:
            NotFoundError: ``social.sanction_not_found``.
            ForbiddenError: ``social.self_sanction_forbidden`` - санкция на самом модераторе;
                ``social.account_sanction_forbidden`` - бан аккаунта без права на него.
            InvalidInputError: ``social.reason_reserved`` - причина с префиксом ``system:``.
        """
        if reason.startswith(rules.SYSTEM_REASON_PREFIX):
            raise InvalidInputError("причина зарезервирована", code=rules.REASON_RESERVED)
        async with self._uow() as uow:
            repo = uow.sanctions
            found = await repo.get(sanction_id)
            if found is None:
                raise NotFoundError("санкция не найдена", code=rules.SANCTION_NOT_FOUND)
            if found.user_id == actor_id:
                raise ForbiddenError("свои санкции не снимают", code=rules.SELF_SANCTION_FORBIDDEN)
            if found.kind == "account" and rules.ACCOUNT_PERM not in actor_perms:
                raise ForbiddenError(
                    "снятие бана аккаунта требует отдельного права",
                    code=rules.ACCOUNT_SANCTION_FORBIDDEN,
                )
            now = self._clock.now()
            version = await repo.lock_and_bump(found.user_id)
            current = await repo.get(sanction_id, for_update=True)
            assert current is not None  # noqa: S101 - записи не удаляются
            if current.revoked_at is not None:
                # Уже закрыта (повтор или гонка): версию не расходуем впустую - откат.
                return current
            await repo.close(sanction_id, at=now, by=actor_id, reason=reason)
            await self._emit(uow, found.user_id, version, actor_id, now)
            closed = await repo.get(sanction_id)
            assert closed is not None  # noqa: S101
            await uow.commit()
        return closed

    # ------------------------------------------------------------------ истечение
    async def expire_due(self, limit: int = EXPIRE_BATCH) -> int:
        """Закрыть истёкшие записи и опубликовать события; число обработанных игроков."""
        async with self._uow() as uow:
            users = await uow.sanctions.users_with_due(self._clock.now(), limit)
        done = 0
        for user_id in users:
            async with self._uow() as uow:
                repo = uow.sanctions
                now = self._clock.now()
                version = await repo.lock_and_bump(user_id)
                due = await repo.due_of_user(user_id, now)
                if not due:
                    continue  # параллельно закрыли (выдача заменила запись)
                for record in due:
                    await repo.close(record.id, at=now, by=None, reason=rules.REVOKE_EXPIRED)
                await self._emit(uow, user_id, version, None, now)
                await uow.commit()
                done += 1
        return done

    # ------------------------------------------------------------------ событие
    async def _emit(
        self,
        uow: SanctionsUnitOfWork,
        user_id: UUID,
        version: int,
        changed_by: UUID | None,
        now: datetime,
    ) -> None:
        active = await uow.sanctions.active(user_id, now)
        uow.publish(
            UserSanctionsChanged(
                aggregate_id=str(user_id),
                account_banned=any(r.kind == "account" for r in active),
                active=[
                    ActiveSanction(expires_at=r.expires_at, kind=r.kind, sanction_id=r.id)
                    for r in active
                ],
                at=now,
                changed_by=changed_by,
                mask=rules.mask_of(r.kind for r in active),
                next_expiry_at=rules.next_expiry(r.expires_at for r in active),
                user_id=user_id,
                version=version,
            )
        )
