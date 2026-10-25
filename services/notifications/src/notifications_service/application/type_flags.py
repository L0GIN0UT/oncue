"""Admin-выключатели типов уведомлений: список и переключение (ADR 0066).

Выключенный тип не порождает уведомлений (ни из события, ни массовой доставкой); настройки
игроков и рассылки администрации не затрагиваются. Переключение идемпотентно по
``Idempotency-Key`` и пишется в аудит (причина - только там).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from gv_core.clock import Clock
from gv_db.idempotency import request_fingerprint, run_idempotent
from notifications_service.application.ports import AdminAction, UnitOfWorkFactory
from notifications_service.application.type_switch import TypeSwitch
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.errors import TypeNotFoundError, TypeNotSwitchableError
from notifications_service.domain.notification import DEFAULT_TYPE_ENABLED, TypeFlag

SET_ENABLED_ACTION = "notification_type.set_enabled"


@dataclass(frozen=True, slots=True)
class TypeState:
    """Тип и его текущая включённость."""

    type_key: str
    enabled: bool
    default_enabled: bool
    changed_by: UUID | None
    changed_at: datetime | None
    switchable: bool = True


def _state(type_key: str, flag: TypeFlag | None, *, switchable: bool = True) -> TypeState:
    return TypeState(
        type_key=type_key,
        enabled=DEFAULT_TYPE_ENABLED if flag is None else flag.enabled,
        default_enabled=DEFAULT_TYPE_ENABLED,
        changed_by=None if flag is None else flag.changed_by,
        changed_at=None if flag is None else flag.changed_at,
        switchable=switchable,
    )


def state_json(state: TypeState) -> dict[str, Any]:  # Any: JSON-ответ идемпотентной мутации
    """Ответ переключения (хранится в ключе идемпотентности)."""
    return {
        "type": state.type_key,
        "enabled": state.enabled,
        "default_enabled": state.default_enabled,
        "changed_by": None if state.changed_by is None else str(state.changed_by),
        "changed_at": None if state.changed_at is None else state.changed_at.isoformat(),
        "switchable": state.switchable,
    }


class NotificationTypeAdmin:
    """Список типов и переключатель включённости."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        catalog: TypeCatalog,
        switch: TypeSwitch,
        clock: Clock,
        new_id: Callable[[], UUID],
    ) -> None:
        self._uow = uow_factory
        self._catalog = catalog
        self._switch = switch
        self._clock = clock
        self._new_id = new_id

    async def states(self) -> list[TypeState]:
        """Все типы каталога с решениями админов (читает БД, не кэш)."""
        async with self._uow() as uow:
            flags = await uow.type_flags.all()
        return [
            _state(t.key, flags.get(t.key), switchable=t.admin_switchable)
            for t in self._catalog.types
        ]

    async def set_enabled(
        self, *, actor_id: UUID, key: str, type_key: str, enabled: bool, reason: str
    ) -> dict[str, Any]:
        """Включить или выключить тип (идемпотентно по ``Idempotency-Key``).

        Raises:
            TypeNotFoundError: типа нет в каталоге.
            TypeNotSwitchableError: системный тип (``admin_switchable = false``).
        """
        notification_type = self._catalog.by_key(type_key)
        if notification_type is None:
            raise TypeNotFoundError
        if not notification_type.admin_switchable:
            raise TypeNotSwitchableError
        fingerprint = request_fingerprint(
            "notification_type.set_enabled", type_key, {"enabled": enabled, "reason": reason}
        )
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                now = self._clock.now()
                previous = (await uow.type_flags.all()).get(type_key)
                flag = TypeFlag(
                    type_key=type_key, enabled=enabled, changed_by=actor_id, changed_at=now
                )
                await uow.type_flags.save(flag)
                await uow.audit.record(
                    AdminAction(
                        id=self._new_id(),
                        actor_id=actor_id,
                        action=SET_ENABLED_ACTION,
                        target=type_key,
                        reason=reason,
                        at=now,
                        details={
                            "enabled": enabled,
                            "previous": DEFAULT_TYPE_ENABLED
                            if previous is None
                            else previous.enabled,
                        },
                    )
                )
                return state_json(_state(type_key, flag, switchable=True))

            response, _ = await run_idempotent(
                uow.idempotency, actor_id, key, fingerprint, perform, status_code=200
            )
            await uow.commit()
        self._switch.invalidate()
        return response
