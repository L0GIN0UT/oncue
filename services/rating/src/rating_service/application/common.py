"""Общее для use-cases rating: контекст admin-мутации, курсоры, JSON-представления."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from gv_core.errors import InvalidInputError
from rating_service.application.ports import AdminAction, Json, RatingUnitOfWork
from rating_service.domain.errors import MatchContractError
from rating_service.domain.ladder import Tier
from rating_service.domain.ladder_line import LadderVersion
from rating_service.domain.season import Season

# Системный актор (запросы, поставленные самим сервисом) и «пустой» пользователь для
# маскировки удалённых аккаунтов в публичных ответах.
SYSTEM_ACTOR = UUID(int=0)
MASKED_USER = UUID(int=0)


@dataclass(frozen=True, slots=True)
class AdminContext:
    """Кто и зачем выполняет admin-мутацию."""

    actor_id: UUID
    reason: str
    idempotency_key: str


async def audit(
    uow: RatingUnitOfWork,
    ctx: AdminContext,
    action: str,
    target: str,
    now: datetime,
    **details: Any,
) -> None:
    """Записать admin-действие в аудит."""
    await uow.audit.record(
        AdminAction(
            actor_id=ctx.actor_id,
            action=action,
            target=target,
            reason=ctx.reason,
            at=now,
            details=details,
        )
    )


def encode_cursor(payload: dict[str, Any]) -> str:
    """Непрозрачный курсор: base64url от JSON."""
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Разобрать курсор.

    Raises:
        InvalidInputError: ``cursor.invalid`` — курсор подделан или повреждён.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidInputError("неверный курсор", code="cursor.invalid") from exc
    if not isinstance(data, dict):
        raise InvalidInputError("неверный курсор", code="cursor.invalid")
    return data


def tier_json(tier: Tier) -> Json:
    """Звание для API (``TierView``)."""
    return {
        "key": tier.key,
        "family": tier.family,
        "step": tier.step,
        "title_key": tier.title_key,
        "below": tier.below,
    }


def season_json(s: Season) -> Json:
    """Сезон для admin-API (``SeasonCard``)."""
    return {
        "season_id": str(s.season_id),
        "ladder": s.ladder,
        "number": s.number,
        "status": s.status.value,
        "starts_at": s.starts_at.isoformat(),
        "ends_at": s.ends_at.isoformat(),
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
    }


def version_json(v: LadderVersion) -> Json:
    """Версия ladder-а с конфигом для admin-API (``LadderVersionBody``)."""
    return {"card": card_json(v), "config": v.config.to_dict()}


def card_json(v: LadderVersion) -> Json:
    """Карточка версии (``LadderVersionCard``)."""
    return {
        "version": v.version,
        "status": v.status.value,
        "created_at": v.created_at.isoformat(),
        "published_at": v.published_at.isoformat() if v.published_at else None,
        "created_by": str(v.created_by),
    }


def require_pair[T](items: list[T]) -> tuple[T, T]:
    """Ровно два элемента (дуэль).

    Raises:
        MatchContractError: игроков не два.
    """
    if len(items) != 2:  # noqa: PLR2004 — дуэль: ровно двое
        raise MatchContractError("рейтинговая дуэль должна иметь ровно двух игроков")
    return items[0], items[1]
