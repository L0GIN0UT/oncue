"""Общее для admin-use-cases: контекст мутации, ошибки, лимиты выдачи, курсор."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from gv_core.errors import ConflictError, InvalidInputError, NotFoundError
from progression_service.application.admin.ports import AdminAction, AdminUnitOfWork
from progression_service.domain.config import CONFIG_INVALID, ConfigInvalidError, ProgressionConfig

GRANT_REASON = "admin_grant"
REVERSAL_REASON = "reversal"
# `source_ref` проводки админа - id записи аудита, а не текст причины: причина (свободный
# текст) живёт только в `admin_actions` и не попадает в журнал и события.
SOURCE_REF_PREFIX = "admin_action:"


class ConfigDraftInvalidError(InvalidInputError):
    """Конфиг не проходит проверку; ``details.errors`` - пути и причины."""

    default_code = CONFIG_INVALID

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__(
            f"конфиг progression невалиден: {len(errors)} ошибок",
            code=CONFIG_INVALID,
            details={"errors": errors},
        )


class ConfigVersionNotFoundError(NotFoundError):
    """Нет такой версии конфига."""

    default_code = "progression.config_version_not_found"

    def __init__(self) -> None:
        super().__init__("версия конфига не найдена")


class ConfigNotDraftError(ConflictError):
    """Публиковать можно только черновик."""

    default_code = "progression.config_not_draft"

    def __init__(self) -> None:
        super().__init__("версия не является черновиком")


class ConfigNeverPublishedError(ConflictError):
    """Откатиться можно только к версии, которая была действующей."""

    default_code = "progression.config_never_published"

    def __init__(self) -> None:
        super().__init__("версия никогда не публиковалась")


class DraftChangedError(ConflictError):
    """Черновик изменился после того, как админ его просмотрел: публикация отклонена."""

    default_code = "progression.draft_changed"

    def __init__(self) -> None:
        super().__init__("черновик изменён после просмотра, перечитайте и повторите")


class NotReversibleLedgerError(ConflictError):
    """Проводку нельзя сторнировать (дебет, сторно, запись журнала опыта)."""

    default_code = "progression.not_reversible"

    def __init__(self, message: str = "проводку нельзя сторнировать") -> None:
        super().__init__(message)


def body_sha256(body: object) -> str:
    """sha256 канонического JSON тела конфига (ключи по порядку, без пробелов)."""
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class UserNotFoundError(NotFoundError):
    """У игрока нет прогресса в progression."""

    default_code = "progression.user_not_found"

    def __init__(self) -> None:
        super().__init__("игрок не найден")


class LedgerEntryNotFoundError(NotFoundError):
    """Нет проводки Версов с таким ``op_id``."""

    default_code = "progression.ledger_entry_not_found"

    def __init__(self) -> None:
        super().__init__("проводка не найдена")


class GrantLimitExceededError(InvalidInputError):
    """Выдача выше лимита операции или суток."""

    default_code = "progression.grant_limit_exceeded"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message, details=details)


class ReasonTooShortError(InvalidInputError):
    """Причина короче ``admin.reason_min_length``."""

    default_code = "progression.reason_too_short"

    def __init__(self, minimum: int) -> None:
        super().__init__(f"причина короче {minimum} символов", details={"min_length": minimum})


@dataclass(frozen=True, slots=True)
class AdminContext:
    """Кто и зачем выполняет admin-мутацию."""

    actor_id: UUID
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AdminLimits:
    """Лимиты ручных операций из блока ``admin`` действующего конфига."""

    grant_max_amount: int
    grant_max_per_user_day: int
    reason_min_length: int

    @classmethod
    def of(cls, config: ProgressionConfig) -> AdminLimits:
        """Прочитать лимиты (``validate_admin_block`` проверил их при сохранении версии)."""
        block = config.body["admin"]
        return cls(
            grant_max_amount=int(block["grant_max_amount"]),
            grant_max_per_user_day=int(block["grant_max_per_user_day"]),
            reason_min_length=int(block["reason_min_length"]),
        )

    def require_reason(self, reason: str) -> None:
        """Причина не короче лимита.

        Raises:
            ReasonTooShortError: причина слишком короткая.
        """
        if len(reason.strip()) < self.reason_min_length:
            raise ReasonTooShortError(self.reason_min_length)


_ADMIN_BOUNDS = {
    "grant_max_amount": (1, 100_000_000),
    "grant_max_per_user_day": (1, 100_000_000),
    "reason_min_length": (1, 200),
}


def validate_config(raw: dict[str, Any]) -> ProgressionConfig:
    """Разобрать и проверить конфиг целиком (домен P1 + блок ``admin``).

    Raises:
        ConfigDraftInvalidError: ``progression.config_invalid`` со списком ``{path, reason}``.
    """
    errors: list[dict[str, str]] = []
    config: ProgressionConfig | None = None
    try:
        config = ProgressionConfig.parse(raw)
    except ConfigInvalidError as exc:
        path, _, reason = exc.message.partition(": ")
        errors.append({"path": path, "reason": reason} if reason else {"path": "", "reason": path})
    errors.extend(_admin_errors(raw.get("admin")))
    if errors or config is None:
        raise ConfigDraftInvalidError(errors)
    return config


def _admin_errors(block: object) -> list[dict[str, str]]:
    if not isinstance(block, dict):
        return [{"path": "admin", "reason": "ожидается объект"}]
    errors: list[dict[str, str]] = []
    for key, (low, high) in _ADMIN_BOUNDS.items():
        value = block.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            errors.append({"path": f"admin.{key}", "reason": f"ожидается целое от {low} до {high}"})
    return errors


async def audit(
    uow: AdminUnitOfWork,
    ctx: AdminContext,
    action: str,
    target: str,
    now: datetime,
    *,
    action_id: UUID,
    **details: Any,
) -> None:
    """Записать admin-действие. Причина живёт только здесь (в журнал и события не попадает)."""
    await uow.audit.record(
        AdminAction(
            id=action_id,
            actor_id=ctx.actor_id,
            action=action,
            target=target,
            reason=ctx.reason,
            at=now,
            details=details,
        )
    )


def encode_cursor(at: datetime, row_id: UUID) -> str:
    """Непрозрачный курсор ``(at, id)``: base64url от JSON."""
    raw = json.dumps({"at": at.isoformat(), "id": str(row_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Разобрать курсор.

    Raises:
        InvalidInputError: ``cursor.invalid`` - курсор повреждён.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        return datetime.fromisoformat(data["at"]), UUID(data["id"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError) as exc:
        raise InvalidInputError("неверный курсор", code="cursor.invalid") from exc
