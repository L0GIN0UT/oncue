"""Кейсы модерации: модель и чистые правила переходов (5c, ADR 0051).

Статусы: ``open -> in_work -> escalated -> resolved`` (из ``in_work`` можно сразу в ``resolved``).
«Старший» (может решать эскалированные кейсы) - обладатель права ``social.sanctions.account``:
оно по миграции identity есть только у admin, у moderator его нет (ADR 0052). Переходы - чистые
функции, возвращающие новое состояние; оптимистичную блокировку (``version``) проверяет
репозиторий при сохранении.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError

OPEN = "open"
IN_WORK = "in_work"
ESCALATED = "escalated"
RESOLVED = "resolved"

DISMISSED = "dismissed"
CONTENT_REMOVED = "content_removed"
SANCTIONED = "sanctioned"

SENIOR_PERM = "social.sanctions.account"

CASE_ALREADY_CLAIMED = "social.case_already_claimed"
CASE_VERSION_CONFLICT = "social.case_version_conflict"
CASE_RESOLVED = "social.case_resolved"
CASE_WRONG_STATE = "social.case_wrong_state"
CASE_NOT_ASSIGNEE = "social.case_not_assignee"
CASE_ESCALATED_FORBIDDEN = "social.case_escalated_forbidden"
CASE_NOT_FOUND = "social.case_not_found"
DECISION_NOT_APPLICABLE = "social.decision_not_applicable"

# Объекты, у которых есть что снимать при ``content_removed`` (у игрока - только санкция).
REMOVABLE_KINDS = frozenset({"collection", "comment", "avatar"})


@dataclass(frozen=True, slots=True)
class ModerationCase:
    """Кейс очереди модерации."""

    id: UUID
    target_kind: str
    target_id: UUID
    target_user_id: UUID
    status: str
    assignee_id: UUID | None
    opened_at: datetime
    version: int
    reports_count: int = 1
    decision: str | None = None
    resolved_by: UUID | None = None
    resolved_at: datetime | None = None


def is_senior(perms: frozenset[str]) -> bool:
    """Старший модератор (admin): решает эскалированные кейсы."""
    return SENIOR_PERM in perms


def ensure_version(case: ModerationCase, version: int) -> None:
    """Оптимистичная блокировка: действие выполняется над версией, которую видел модератор."""
    if case.version != version:
        raise ConflictError("кейс изменён, обновите карточку", code=CASE_VERSION_CONFLICT)


def _ensure_not_resolved(case: ModerationCase) -> None:
    if case.status == RESOLVED:
        raise ConflictError("кейс уже решён", code=CASE_RESOLVED)


def claim(case: ModerationCase, actor: UUID, version: int) -> ModerationCase:
    """Взять в работу: ``open -> in_work``. Повторное взятие своим - без изменений."""
    _ensure_not_resolved(case)
    if case.status == IN_WORK and case.assignee_id == actor:
        return case
    if case.status != OPEN:
        if case.assignee_id is not None and case.assignee_id != actor:
            raise ConflictError("кейс уже взят другим модератором", code=CASE_ALREADY_CLAIMED)
        raise ConflictError("кейс нельзя взять в этом статусе", code=CASE_WRONG_STATE)
    ensure_version(case, version)
    return replace(case, status=IN_WORK, assignee_id=actor, version=case.version + 1)


def escalate(case: ModerationCase, actor: UUID, version: int) -> ModerationCase:
    """Эскалировать: ``in_work -> escalated``; исполнитель - тот, кто взял кейс."""
    _ensure_not_resolved(case)
    if case.status != IN_WORK:
        raise ConflictError("эскалировать можно кейс в работе", code=CASE_WRONG_STATE)
    if case.assignee_id != actor:
        raise ForbiddenError("кейс ведёт другой модератор", code=CASE_NOT_ASSIGNEE)
    ensure_version(case, version)
    return replace(case, status=ESCALATED, version=case.version + 1)


def check_decision(case: ModerationCase, decision: str) -> None:
    """Решение применимо к объекту: удалять нечего у игрока."""
    if decision == CONTENT_REMOVED and case.target_kind not in REMOVABLE_KINDS:
        raise InvalidInputError(
            "у игрока нет контента для удаления; выберите санкцию", code=DECISION_NOT_APPLICABLE
        )


def resolve(  # noqa: PLR0917 - поля решения
    case: ModerationCase,
    actor: UUID,
    perms: frozenset[str],
    version: int,
    decision: str,
    now: datetime,
) -> ModerationCase:
    """Решить кейс. ``in_work`` - его исполнителем, ``escalated`` - только старшим."""
    _ensure_not_resolved(case)
    if case.status == OPEN:
        raise ConflictError("сначала возьмите кейс в работу", code=CASE_WRONG_STATE)
    if case.status == ESCALATED:
        if not is_senior(perms):
            raise ForbiddenError(
                "эскалированный кейс решает только старший модератор",
                code=CASE_ESCALATED_FORBIDDEN,
            )
    elif case.assignee_id != actor:
        raise ConflictError("кейс уже взят другим модератором", code=CASE_ALREADY_CLAIMED)
    ensure_version(case, version)
    check_decision(case, decision)
    return replace(
        case,
        status=RESOLVED,
        decision=decision,
        resolved_by=actor,
        resolved_at=now,
        version=case.version + 1,
    )
