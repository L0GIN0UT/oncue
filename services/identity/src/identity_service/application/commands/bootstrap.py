"""Идемпотентное создание bootstrap-админа для автосидинга (только local/ci).

Use-case повторяет обычные доменные инварианты (агрегат ``User``, матрица ролей, порт
хэшера), не обходя их: роль ``admin`` выдаётся через ``User.change_roles`` с записью аудита,
пароль — через тот же argon2-хэшер, что и остальной сервис. Запускается командой ``migrate``
(composition root — ``wiring.ensure_bootstrap_admin``) и обязан быть идемпотентным: повторный
запуск не создаёт второго пользователя и не падает.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.ids import uuid7
from identity_service.application.commands.auth import ADMIN_ROLE
from identity_service.application.ports import (
    IdentityUnitOfWork,
    PasswordHasher,
    RoleChange,
    UnitOfWorkFactory,
)
from identity_service.domain import (
    DEFAULT_ROLE,
    DisplayName,
    EmailAddress,
    Nickname,
    PasswordPolicy,
    RoleMatrix,
    User,
)

log = structlog.get_logger(__name__)

BOOTSTRAP_REASON = "bootstrap_admin"
DEFAULT_NICKNAME = "admin"


class EnsureBootstrapAdmin:
    """Гарантировать существование активного админа с заданными логином и паролем.

    Идемпотентность: по e-mail. Нет пользователя — создаётся новый (e-mail подтверждён,
    статус активен, роль ``admin``, пароль задан). Пользователь уже есть — ему добавляется
    роль ``admin`` и подтверждается e-mail, но **пароль не трогается** (его задаём только при
    создании): чужой аккаунт с этим e-mail не должен быть захвачен подстановкой пароля, даже
    если local/ci-процесс по ошибке смотрит в боевую БД. Логин — служебный ``*.local``-адрес,
    коллизия с реальным пользователем крайне маловероятна, а запись аудита делает выдачу роли
    прослеживаемой.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        hasher: PasswordHasher,
        clock: Clock,
        password_policy: PasswordPolicy,
    ) -> None:
        self._uow, self._hasher, self._clock = uow, hasher, clock
        self._policy = password_policy

    async def __call__(self, *, login: str, password: str, nickname: str | None = None) -> UUID:
        """Создать или дополнить админа; возвращает его id.

        Raises:
            InvalidInputError: ``auth.password_weak`` — пароль не проходит политику;
                ``user.email_invalid`` / ``user.nickname_invalid`` — некорректные логин/ник.
        """
        email = EmailAddress.parse(login)
        nick = Nickname.parse(nickname or DEFAULT_NICKNAME)
        # Та же политика, что у регистрации: слабый bootstrap-пароль — ошибка конфигурации.
        self._policy.check(password, nickname=nick.value, email=email.value)
        # argon2 — вне транзакции (как в регистрации): долгое вычисление не держит строки БД.
        password_hash = await self._hasher.hash(password)
        now = self._clock.now()

        async with self._uow() as uow:
            matrix = await uow.roles.get_matrix()
            user = await uow.users.find_by_email(email)
            if user is None:
                user = User.register(
                    uuid7(),
                    nickname=nick,
                    email=email,
                    display_name=DisplayName.parse(nick.value),
                    locale=None,
                    roles=frozenset({DEFAULT_ROLE}),
                    now=now,
                )
                user.verify_email(now)
                await self._grant_admin(uow, user, matrix, now)
                await uow.users.add(user)
                await uow.credentials.set_password_hash(user.id, password_hash, now)
                uow.track(user)
                await uow.commit()
                log.warning("auth.bootstrap_admin_created", user_id=str(user.id))
                return user.id

            # Пароль существующему аккаунту не задаём (см. докстринг): если это не наш
            # bootstrap-админ, подстановка пароля была бы захватом чужого аккаунта.
            changed = user.verify_email(now)
            changed = await self._grant_admin(uow, user, matrix, now) or changed
            if changed:
                await uow.users.save(user)
                uow.track(user)
            await uow.commit()
            log.warning("auth.bootstrap_admin_ensured", user_id=str(user.id), changed=changed)
            return user.id

    async def _grant_admin(
        self, uow: IdentityUnitOfWork, user: User, matrix: RoleMatrix, now: datetime
    ) -> bool:
        """Добавить роль ``admin`` с записью аудита. ``False`` — роль уже была."""
        before = sorted(user.roles)
        if not user.change_roles(
            user.roles | {ADMIN_ROLE}, matrix, actor_id=None, reason=BOOTSTRAP_REASON
        ):
            return False
        await uow.roles.add_audit(
            RoleChange(
                id=uuid7(),
                kind="user_roles",
                user_id=user.id,
                actor_id=None,
                before={"roles": before},
                after={"roles": sorted(user.roles)},
                reason=BOOTSTRAP_REASON,
                created_at=now,
            )
        )
        return True
