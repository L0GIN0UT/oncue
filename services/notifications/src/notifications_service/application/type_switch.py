"""Выключатели типов уведомлений: чтение с коротким кэшем (ADR 0066).

Решение админа хранится в ``notification_type_flags``; нет записи - тип включён. Кэш живёт
``ttl_s`` секунд в процессе: другой процесс (consumer, воркер) подхватит изменение не позже чем
через TTL, процесс, в котором меняли, - сразу (``invalidate``). ``ttl_s = 0`` - без кэша.
"""

from __future__ import annotations

from gv_core.clock import Clock
from notifications_service.application.ports import UnitOfWork
from notifications_service.domain.notification import DEFAULT_TYPE_ENABLED, TypeFlag


class TypeSwitch:
    """Включён ли тип. Читает флаги через переданный UoW (своей транзакции не открывает)."""

    def __init__(self, clock: Clock, *, ttl_s: float) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._flags: dict[str, TypeFlag] = {}
        self._loaded_at: float | None = None

    def invalidate(self) -> None:
        """Забыть кэш (после изменения в этом процессе)."""
        self._loaded_at = None

    async def flags(self, uow: UnitOfWork) -> dict[str, TypeFlag]:
        """Решения админов по ключам типов (свежие или из кэша)."""
        now = self._clock.now().timestamp()
        if self._loaded_at is None or self._ttl_s <= 0 or now - self._loaded_at >= self._ttl_s:
            self._flags = await uow.type_flags.all()
            self._loaded_at = now
        return self._flags

    async def is_enabled(self, uow: UnitOfWork, type_key: str) -> bool:
        """Тип включён (нет решения - по умолчанию включён)."""
        flag = (await self.flags(uow)).get(type_key)
        return DEFAULT_TYPE_ENABLED if flag is None else flag.enabled
