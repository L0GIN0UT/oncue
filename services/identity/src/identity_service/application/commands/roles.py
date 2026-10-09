"""Назначение ролей и изменение матрицы ролей (admin API)."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7
from identity_service.application.dto import Actor
from identity_service.application.ports import RoleChange, UnitOfWorkFactory
from identity_service.domain import DEFAULT_ROLE, RoleMatrix

log = structlog.get_logger(__name__)

MANAGE_PERM = "roles.manage"
_REASON_MAX = 500


def _reason(raw: str) -> str:
    reason = raw.strip()
    if not reason or len(reason) > _REASON_MAX:
        raise InvalidInputError("укажите причину (до 500 символов)", code="admin.reason_required")
    return reason


class AssignRoles:
    """Заменить роли пользователя. Нельзя выдать или снять роль с правами шире своих."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    async def __call__(
        self, actor: Actor, user_id: UUID, roles: frozenset[str], reason: str
    ) -> frozenset[str]:
        """Назначить роли; возвращает итоговые права пользователя.

        Raises:
            NotFoundError: ``user.not_found``.
            ForbiddenError: ``roles.escalation`` — права затронутых ролей шире прав актора.
        """
        reason = _reason(reason)
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.is_active:
                raise NotFoundError("пользователь не найден", code="user.not_found")
            matrix = await uow.roles.get_matrix()
            roles = matrix.require_known(roles | {DEFAULT_ROLE})
            before = user.roles
            if not matrix.can_grant(actor.perms, roles ^ before):
                raise ForbiddenError(
                    "нельзя выдавать или снимать роли с правами шире своих",
                    code="roles.escalation",
                )
            if user.change_roles(roles, matrix, actor_id=actor.user_id, reason=reason):
                await uow.roles.add_audit(
                    RoleChange(
                        id=uuid7(),
                        kind="user_roles",
                        user_id=user.id,
                        actor_id=actor.user_id,
                        before={"roles": sorted(before)},
                        after={"roles": sorted(roles)},
                        reason=reason,
                        created_at=self._clock.now(),
                    )
                )
                await uow.users.save(user)
                uow.track(user)
                await uow.commit()
                log.info(
                    "roles.assigned",
                    user_id=str(user.id),
                    actor_id=str(actor.user_id),
                    roles=sorted(roles),
                )
            return matrix.effective_perms(user.roles)


class UpdateRoleMatrix:
    """Заменить матрицу ролей и опубликовать новые права затронутых пользователей."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    async def __call__(
        self, actor: Actor, roles: Mapping[str, frozenset[str]], reason: str
    ) -> RoleMatrix:
        """Применить новую матрицу.

        Raises:
            InvalidInputError: неизвестные права, некорректные имена, нет роли ``user``.
            ForbiddenError: ``roles.escalation`` или ``roles.lockout``.
            ConflictError: ``roles.in_use`` — удаляемая роль назначена пользователям.
        """
        reason = _reason(reason)
        async with self._uow() as uow:
            current = await uow.roles.get_matrix()
            updated = current.replace(roles)
            if DEFAULT_ROLE not in updated.roles:
                raise InvalidInputError("роль user обязательна", code="roles.default_required")
            changed = {
                name
                for name in current.roles.keys() | updated.roles.keys()
                if current.roles.get(name) != updated.roles.get(name)
            }
            touched_perms = set().union(
                *(
                    current.roles.get(n, frozenset()) ^ updated.roles.get(n, frozenset())
                    for n in changed
                )
            )
            if not touched_perms <= actor.perms:
                raise ForbiddenError("нельзя менять права шире своих", code="roles.escalation")
            if MANAGE_PERM not in updated.effective_perms(actor.roles):
                raise ForbiddenError(
                    "нельзя лишить себя права управлять ролями", code="roles.lockout"
                )
            removed = current.roles.keys() - updated.roles.keys()
            if removed and await uow.users.ids_with_roles(removed):
                raise ConflictError(
                    "роль назначена пользователям",
                    code="roles.in_use",
                    details={"roles": sorted(removed)},
                )
            if not changed:
                return current
            affected = await uow.users.ids_with_roles(changed)
            await uow.roles.save_matrix(updated)
            await uow.roles.add_audit(
                RoleChange(
                    id=uuid7(),
                    kind="matrix",
                    user_id=None,
                    actor_id=actor.user_id,
                    before={n: sorted(current.roles.get(n, ())) for n in sorted(changed)},
                    after={n: sorted(updated.roles.get(n, ())) for n in sorted(changed)},
                    reason=reason,
                    created_at=self._clock.now(),
                )
            )
            # Полный снимок прав каждого затронутого пользователя (UserRolesChanged).
            # Этап 1: пользователей с ролями модерации единицы; массовый случай — задача
            # воркера в следующих этапах.
            for user_id in affected:
                user = await uow.users.get(user_id)
                if user is None or not user.is_active:
                    continue
                user.announce_perms(updated, actor_id=actor.user_id, reason=reason)
                await uow.users.save(user)
                uow.track(user)
            await uow.commit()
        log.info("roles.matrix_updated", actor_id=str(actor.user_id), roles=sorted(changed))
        return updated
