"""Запись в аудит из представлений админки."""

from __future__ import annotations

from typing import Any

from admin_service.audit.models import AuditEntry
from admin_service.core.models import AdminUser


def record(
    *,
    actor: AdminUser,
    action: str,
    service: str,
    target_type: str,
    target_id: str,
    reason: str,
    request_id: str,
    idempotency_key: str,
    before: Any = None,
    after: Any = None,
    error_code: str | None = None,
) -> AuditEntry:
    """Записать действие. ``error_code`` задан — действие отклонено сервисом."""
    return AuditEntry.objects.create(
        actor=actor,
        actor_identity_id=actor.identity_user_id,
        action=action,
        service=service,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        before=before,
        after=after,
        request_id=request_id,
        idempotency_key=idempotency_key,
        outcome=AuditEntry.Outcome.FAILED if error_code else AuditEntry.Outcome.SUCCEEDED,
        error_code=error_code or "",
    )
