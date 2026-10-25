"""Повторный анонс прав сотрудников (бэкфилл проекций ``UserRolesChanged``).

Потребители (social — иерархия модераторов, ADR 0058; admin — зеркало) строят проекцию прав
из событий. Сотрудники, получившие роли до появления проекции, в неё не попали, а события не
воспроизводятся. Команда публикует полный снимок прав каждого активного пользователя с ролью,
отличной от ``user``; потребители идемпотентны по ``version``, повтор безопасен.
"""

from __future__ import annotations

import structlog

from identity_service.application.ports import UnitOfWorkFactory
from identity_service.domain import DEFAULT_ROLE

log = structlog.get_logger(__name__)

BACKFILL_REASON = "staff_perms_backfill"


class ReannounceStaffPerms:
    """Опубликовать ``UserRolesChanged`` всем активным сотрудникам."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def __call__(self) -> int:
        """Число сотрудников, права которых проанонсированы заново."""
        announced = 0
        async with self._uow() as uow:
            matrix = await uow.roles.get_matrix()
            staff_roles = set(matrix.roles) - {DEFAULT_ROLE}
            for user_id in await uow.users.ids_with_roles(staff_roles):
                user = await uow.users.get(user_id)
                if user is None or not user.is_active:
                    continue
                user.announce_perms(matrix, actor_id=None, reason=BACKFILL_REASON)
                await uow.users.save(user)
                uow.track(user)
                announced += 1
            await uow.commit()
        log.info("identity.staff_perms_reannounced", users=announced)
        return announced
