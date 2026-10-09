"""Назначение ролей и изменение матрицы ролей (admin API).

Мутации идемпотентны по ``(actor_id, Idempotency-Key)`` (gv_db.idempotency): ключ и ответ
сохраняются в той же транзакции, что и изменение, повтор возвращает сохранённый ответ.

Понижение прав (пользователь теряет хотя бы одно право) отзывает все его сессии и кладёт
``sid`` в denylist: иначе уже выданные access-токены со старыми правами работали бы до
истечения, а refresh — продолжал бы сессию, открытую под старые права.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from identity_service.application.commands.auth import revoke_all_sessions, revoke_quietly
from identity_service.application.dto import Actor
from identity_service.application.ports import (
    IdentityUnitOfWork,
    RevocationStore,
    RoleChange,
    UnitOfWorkFactory,
)
from identity_service.application.queries import build_user_view, linked_accounts
from identity_service.domain import DEFAULT_ROLE, RoleMatrix, User

log = structlog.get_logger(__name__)

MANAGE_PERM = "roles.manage"
REVOKE_REASON = "perms_reduced"
_REASON_MAX = 500


def _reason(raw: str) -> str:
    reason = raw.strip()
    if not reason or len(reason) > _REASON_MAX:
        raise InvalidInputError("укажите причину (до 500 символов)", code="admin.reason_required")
    return reason


def matrix_to_json(matrix: RoleMatrix) -> dict[str, Any]:
    """Матрица как JSON-объект (ответ мутации, сохраняемый для идемпотентности)."""
    return {
        "roles": {name: sorted(perms) for name, perms in sorted(matrix.roles.items())},
        "permissions": sorted(matrix.permissions),
    }


async def _revoke_if_reduced(
    uow: IdentityUnitOfWork,
    user: User,
    before: frozenset[str],
    after: frozenset[str],
    clock: Clock,
) -> list[UUID]:
    """Отозвать сессии пользователя, если он потерял хотя бы одно право."""
    if not before - after:
        return []
    return await revoke_all_sessions(uow, user.id, clock, REVOKE_REASON)


class AssignRoles:
    """Заменить роли пользователя. Нельзя выдать или снять роль с правами шире своих."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, revocation: RevocationStore) -> None:
        self._uow, self._clock, self._revocation = uow, clock, revocation

    async def __call__(
        self,
        actor: Actor,
        user_id: UUID,
        roles: frozenset[str],
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Назначить роли; возвращает пользователя (``UserView.to_json``).

        Raises:
            NotFoundError: ``user.not_found``.
            ForbiddenError: ``roles.escalation`` — права затронутых ролей шире прав актора.
            ConflictError: ``idempotency.key_reused`` / ``idempotency.in_progress``.
        """
        reason = _reason(reason)
        fingerprint = request_fingerprint(
            "user.roles.assign", user_id, {"roles": sorted(roles), "reason": reason}
        )
        revoked: list[UUID] = []
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                nonlocal revoked
                user = await uow.users.get(user_id)
                if user is None or not user.is_active:
                    raise NotFoundError("пользователь не найден", code="user.not_found")
                matrix = await uow.roles.get_matrix()
                wanted = matrix.require_known(roles | {DEFAULT_ROLE})
                before = user.roles
                if not matrix.can_grant(actor.perms, wanted ^ before):
                    raise ForbiddenError(
                        "нельзя выдавать или снимать роли с правами шире своих",
                        code="roles.escalation",
                    )
                if user.change_roles(wanted, matrix, actor_id=actor.user_id, reason=reason):
                    await uow.roles.add_audit(
                        RoleChange(
                            id=uuid7(),
                            kind="user_roles",
                            user_id=user.id,
                            actor_id=actor.user_id,
                            before={"roles": sorted(before)},
                            after={"roles": sorted(wanted)},
                            reason=reason,
                            created_at=self._clock.now(),
                        )
                    )
                    await uow.users.save(user)
                    uow.track(user)
                    revoked = await _revoke_if_reduced(
                        uow,
                        user,
                        matrix.effective_perms(before),
                        matrix.effective_perms(wanted),
                        self._clock,
                    )
                    log.info(
                        "roles.assigned",
                        user_id=str(user.id),
                        actor_id=str(actor.user_id),
                        roles=sorted(wanted),
                        sessions_revoked=len(revoked),
                    )
                view = build_user_view(user, matrix, await linked_accounts(uow, user.id))
                return view.to_json()

            response, _ = await run_idempotent(
                uow.idempotency, actor.user_id, idempotency_key, fingerprint, perform
            )
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        return response


class UpdateRoleMatrix:
    """Заменить матрицу ролей и опубликовать новые права затронутых пользователей."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, revocation: RevocationStore) -> None:
        self._uow, self._clock, self._revocation = uow, clock, revocation

    async def __call__(
        self,
        actor: Actor,
        roles: Mapping[str, frozenset[str]],
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Применить новую матрицу; возвращает её (``matrix_to_json``).

        Raises:
            InvalidInputError: неизвестные права, некорректные имена, нет роли ``user``.
            ForbiddenError: ``roles.escalation`` или ``roles.lockout``.
            ConflictError: ``roles.in_use`` — удаляемая роль назначена пользователям;
                ``idempotency.key_reused`` / ``idempotency.in_progress``.
        """
        reason = _reason(reason)
        fingerprint = request_fingerprint(
            "roles.matrix.update",
            None,
            {"roles": {k: sorted(v) for k, v in sorted(roles.items())}, "reason": reason},
        )
        revoked: list[UUID] = []
        async with self._uow() as uow:

            async def perform() -> dict[str, Any]:
                nonlocal revoked
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
                    return matrix_to_json(current)
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
                for affected_id in affected:
                    user = await uow.users.get(affected_id)
                    if user is None or not user.is_active:
                        continue
                    user.announce_perms(updated, actor_id=actor.user_id, reason=reason)
                    await uow.users.save(user)
                    uow.track(user)
                    revoked += await _revoke_if_reduced(
                        uow,
                        user,
                        current.effective_perms(user.roles),
                        updated.effective_perms(user.roles),
                        self._clock,
                    )
                log.info(
                    "roles.matrix_updated",
                    actor_id=str(actor.user_id),
                    roles=sorted(changed),
                    sessions_revoked=len(revoked),
                )
                return matrix_to_json(updated)

            response, _ = await run_idempotent(
                uow.idempotency, actor.user_id, idempotency_key, fingerprint, perform
            )
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        return response
