"""Очередь модерации: список, карточка, взять, эскалировать, решить (5c, ADR 0051, 0052).

Каждое действие - одна транзакция: кейс (оптимистичная блокировка по ``version``), контент,
санкция (через ``SanctionGranter`` в той же транзакции), аудит и события outbox. Мутации
идемпотентны по ``(модератор, Idempotency-Key)``. Тексты жалоб возвращаются только в карточке
(админ-ответ) и в события не попадают. Ответы - JSON-совместимые словари: их же хранит
таблица идемпотентности, а слой API строит из них схемы.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import ConflictError, InvalidInputError, NotFoundError
from gv_core.pagination import clamp_limit, decode_cursor, encode_cursor
from gv_db.idempotency import request_fingerprint, run_idempotent
from social_service.application.ports.cases import AuditEntry, CaseReportRow, GranterFactory
from social_service.application.ports.discussion import DiscussionUnitOfWork, DiscussionUowFactory
from social_service.application.ports.sanctions import GrantRequest
from social_service.domain import cases as rules
from social_service.domain import discussion as discussion_rules
from social_service.domain.cases import ModerationCase
from social_service.domain.discussion_events import CommentDeleted, ContentModerated, ReportResolved

type Json = dict[str, Any]


@dataclass(frozen=True, slots=True)
class Staff:
    """Модератор: идентификатор и права из токена."""

    user_id: UUID
    perms: frozenset[str]


@dataclass(frozen=True, slots=True)
class SanctionSpec:
    """Санкция, выдаваемая вместе с решением ``sanctioned``."""

    kind: str
    duration_s: int | None
    reason: str


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def summary(case: ModerationCase) -> Json:
    """Кейс в списке (``CaseSummary``)."""
    return {
        "id": str(case.id),
        "target_kind": case.target_kind,
        "target_id": str(case.target_id),
        "target_user_id": str(case.target_user_id),
        "status": case.status,
        "reports_count": case.reports_count,
        "assignee_id": str(case.assignee_id) if case.assignee_id else None,
        "opened_at": case.opened_at.isoformat(),
        "version": case.version,
    }


def _report(r: CaseReportRow) -> Json:
    return {
        "id": str(r.id),
        "author_id": str(r.author_id),
        "reason_code": r.reason_code,
        "text": r.text,
        "context_match_id": str(r.context_match_id) if r.context_match_id else None,
        "created_at": r.created_at.isoformat(),
    }


def _detail(case: ModerationCase, reports: list[CaseReportRow], preview: str | None) -> Json:
    return {
        **summary(case),
        "reports": [_report(r) for r in reports],
        "target_preview": preview,
        "decision": case.decision,
        "resolved_by": str(case.resolved_by) if case.resolved_by else None,
        "resolved_at": _iso(case.resolved_at),
    }


def _decode(cursor: str) -> tuple[datetime, UUID]:
    data = decode_cursor(cursor)
    try:
        return datetime.fromisoformat(data["t"]), UUID(data["i"])
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidInputError("неверный курсор", code="pagination.bad_cursor") from exc


class CaseService:
    """Use-cases очереди модерации."""

    def __init__(
        self,
        uow_factory: DiscussionUowFactory,
        granter_factory: GranterFactory,
        clock: Clock,
        new_id: Callable[[], UUID],
    ) -> None:
        self._uow = uow_factory
        self._granter = granter_factory
        self._clock = clock
        self._new_id = new_id

    async def queue(
        self,
        staff: Staff,
        *,
        status: str | None,
        target_kind: str | None,
        mine: bool,
        cursor: str | None,
        limit: int,
    ) -> Json:
        """Очередь: старые первыми; ``mine`` - только взятые мной."""
        after = _decode(cursor) if cursor else None
        size = clamp_limit(limit)
        async with self._uow() as uow:
            rows = await uow.cases.page(
                status=status,
                target_kind=target_kind,
                assignee_id=staff.user_id if mine else None,
                after=after,
                limit=size + 1,
            )
        has_more = len(rows) > size
        rows = rows[:size]
        next_cursor = (
            encode_cursor({"t": rows[-1].opened_at.isoformat(), "i": str(rows[-1].id)})
            if has_more
            else None
        )
        return {"items": [summary(c) for c in rows], "next_cursor": next_cursor}

    async def detail(self, case_id: UUID) -> Json:
        """Карточка кейса с жалобами и фрагментом содержимого."""
        async with self._uow() as uow:
            case = await self._load(uow, case_id)
            return await self._card(uow, case)

    async def claim(self, staff: Staff, case_id: UUID, version: int, key: str) -> Json:
        """Взять кейс в работу."""
        fingerprint = request_fingerprint("case.claim", case_id, {"version": version})
        async with self._uow() as uow:

            async def perform() -> Json:
                case = await self._load(uow, case_id, for_update=True)
                updated = rules.claim(case, staff.user_id, version)
                if updated is not case:
                    await self._save(uow, updated, case.version)
                    await self._audit(uow, staff, "case.claim", case_id, "", {})
                return summary(updated)

            data, _ = await run_idempotent(
                uow.idempotency, staff.user_id, key, fingerprint, perform
            )
            await uow.commit()
        return data

    async def escalate(
        self, staff: Staff, case_id: UUID, version: int, reason: str, key: str
    ) -> Json:
        """Эскалировать кейс старшему."""
        fingerprint = request_fingerprint(
            "case.escalate", case_id, {"version": version, "reason": reason}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                case = await self._load(uow, case_id, for_update=True)
                updated = rules.escalate(case, staff.user_id, version)
                await self._save(uow, updated, case.version)
                await self._audit(uow, staff, "case.escalate", case_id, reason, {})
                return summary(updated)

            data, _ = await run_idempotent(
                uow.idempotency, staff.user_id, key, fingerprint, perform
            )
            await uow.commit()
        return data

    async def resolve(
        self,
        staff: Staff,
        case_id: UUID,
        *,
        version: int,
        decision: str,
        sanction: SanctionSpec | None,
        reason: str,
        key: str,
    ) -> Json:
        """Решить кейс: контент, санкция, аудит и события - одной транзакцией."""
        fingerprint = request_fingerprint(
            "case.resolve",
            case_id,
            {
                "version": version,
                "decision": decision,
                "reason": reason,
                "sanction": None
                if sanction is None
                else [sanction.kind, sanction.duration_s, sanction.reason],
            },
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                case = await self._load(uow, case_id, for_update=True)
                now = self._clock.now()
                resolved = rules.resolve(case, staff.user_id, staff.perms, version, decision, now)
                if decision == rules.CONTENT_REMOVED and case.target_kind == "comment":
                    await self._hide_comment(uow, case, staff, now)
                if sanction is not None:
                    await self._granter(uow).grant(
                        GrantRequest(
                            actor_id=staff.user_id,
                            actor_perms=staff.perms,
                            user_id=case.target_user_id,
                            kind=sanction.kind,  # type: ignore[arg-type]  # Literal проверен схемой API
                            duration_s=sanction.duration_s,
                            reason=sanction.reason,
                        )
                    )
                await self._save(uow, resolved, case.version)
                reports = await uow.cases.reports(case_id)
                await self._audit(
                    uow,
                    staff,
                    "case.resolve",
                    case_id,
                    reason,
                    {"decision": decision, "sanction_kind": sanction.kind if sanction else None},
                )
                self._emit_resolution(uow, resolved, reports, staff, now)
                return await self._card(uow, resolved, reports)

            data, _ = await run_idempotent(
                uow.idempotency, staff.user_id, key, fingerprint, perform
            )
            await uow.commit()
        return data

    # ------------------------------------------------------------------ внутреннее
    async def _load(
        self, uow: DiscussionUnitOfWork, case_id: UUID, *, for_update: bool = False
    ) -> ModerationCase:
        case = await uow.cases.get(case_id, for_update=for_update)
        if case is None:
            raise NotFoundError("кейс не найден", code=rules.CASE_NOT_FOUND)
        return case

    async def _card(
        self,
        uow: DiscussionUnitOfWork,
        case: ModerationCase,
        reports: list[CaseReportRow] | None = None,
    ) -> Json:
        rows = reports if reports is not None else await uow.cases.reports(case.id)
        preview = await uow.cases.preview(case.target_kind, case.target_id)
        return _detail(case, rows, preview)

    async def _save(self, uow: DiscussionUnitOfWork, case: ModerationCase, expected: int) -> None:
        if not await uow.cases.save(case, expected_version=expected):
            # Строка заблокирована FOR UPDATE, так что сюда попадают только сбои согласованности.
            raise ConflictError("кейс изменён, обновите карточку", code=rules.CASE_VERSION_CONFLICT)

    async def _audit(  # noqa: PLR0917 - поля записи аудита
        self,
        uow: DiscussionUnitOfWork,
        staff: Staff,
        action: str,
        case_id: UUID,
        reason: str,
        details: Json,
    ) -> None:
        await uow.audit.add(
            AuditEntry(
                id=self._new_id(),
                actor_id=staff.user_id,
                action=action,
                target=f"case:{case_id}",
                reason=reason,
                at=self._clock.now(),
                details=details,
            )
        )

    async def _hide_comment(
        self, uow: DiscussionUnitOfWork, case: ModerationCase, staff: Staff, now: datetime
    ) -> None:
        comment = await uow.comments.get(case.target_id, for_update=True)
        if comment is None or comment.is_deleted:
            return
        await uow.comments.mark_deleted(
            comment.id, at=now, by=staff.user_id, role=discussion_rules.ROLE_MODERATOR
        )
        uow.emit(
            CommentDeleted(
                aggregate_id=str(comment.id),
                at=now,
                by_role=discussion_rules.ROLE_MODERATOR,
                collection_id=comment.collection_id,
                comment_id=comment.id,
                deleted_by=staff.user_id,
            )
        )

    def _emit_resolution(
        self,
        uow: DiscussionUnitOfWork,
        case: ModerationCase,
        reports: list[CaseReportRow],
        staff: Staff,
        now: datetime,
    ) -> None:
        decision = case.decision or ""
        uow.emit(
            ContentModerated(
                aggregate_id=str(case.id),
                at=now,
                case_id=case.id,
                decision=decision,
                moderator_id=staff.user_id,
                target_id=case.target_id,
                target_kind=case.target_kind,
                target_user_id=case.target_user_id,
            )
        )
        for report in reports:
            uow.emit(
                ReportResolved(
                    aggregate_id=str(report.id),
                    at=now,
                    case_id=case.id,
                    decision=decision,
                    report_id=report.id,
                    reporter_id=report.author_id,
                )
            )
