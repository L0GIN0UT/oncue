"""Регистрация, подтверждение e-mail, вход паролем, refresh, выход, сброс пароля."""

from __future__ import annotations

import re
from datetime import timedelta
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    UnauthenticatedError,
)
from gv_core.ids import uuid7
from identity_service.application.config import AuthConfig
from identity_service.application.dto import RegisterCommand
from identity_service.application.ports import (
    EmailMessage,
    EmailSender,
    EmailToken,
    EmailTokenPurpose,
    IdentityUnitOfWork,
    PasswordHasher,
    RateLimiter,
    RevocationStore,
    RoleChange,
    SecretGenerator,
    UnitOfWorkFactory,
)
from identity_service.application.security import ensure_allowed, hash_secret, rate_key
from identity_service.application.sessions import IssuedSession, SessionIssuer
from identity_service.domain import (
    DEFAULT_ROLE,
    Consent,
    DisplayName,
    EmailAddress,
    Nickname,
    ReuseDetected,
    SessionKind,
    User,
    missing_consents,
    rotate_refresh_token,
)

log = structlog.get_logger(__name__)

EMAIL_TOKEN_BYTES = 32
ADMIN_ROLE = "admin"
ADMIN_ACCESS_PERM = "admin.access"
_LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$")
_INVALID_CREDENTIALS = "неверный логин или пароль"


def _invalid_credentials() -> UnauthenticatedError:
    # Один и тот же ответ для «нет пользователя» и «неверный пароль» (§8 п.1).
    return UnauthenticatedError(_INVALID_CREDENTIALS, code="auth.invalid_credentials")


def _parse_locale(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    if len(raw) > 16 or not _LOCALE_RE.fullmatch(raw):  # noqa: PLR2004
        raise InvalidInputError("некорректная локаль", code="user.locale_invalid")
    return raw


async def _issue_email_token(
    uow: IdentityUnitOfWork,
    secrets: SecretGenerator,
    clock: Clock,
    *,
    user_id: UUID,
    purpose: EmailTokenPurpose,
    ttl_s: int,
) -> str:
    secret = secrets.token(EMAIL_TOKEN_BYTES)
    now = clock.now()
    await uow.email_tokens.add(
        EmailToken(
            id=uuid7(),
            user_id=user_id,
            purpose=purpose,
            token_hash=hash_secret(secret),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_s),
        )
    )
    return secret


async def _send_safely(sender: EmailSender, message: EmailMessage) -> None:
    """Письмо после коммита: сбой отправки не откатывает операцию, но виден в логах."""
    try:
        await sender.send(message)
    except Exception as exc:
        log.warning("email.send_failed", purpose=message.purpose, error=type(exc).__name__)


async def revoke_quietly(store: RevocationStore, session_ids: list[UUID]) -> None:
    """Denylist после коммита. Сбой Redis не отменяет отзыв в БД: refresh уже не работает,
    а выданные access-токены истекут сами (≤ access TTL)."""
    if not session_ids:
        return
    try:
        await store.revoke(session_ids)
    except Exception as exc:
        log.warning(
            "auth.denylist_write_failed", sessions=len(session_ids), error=type(exc).__name__
        )


class RegisterWithEmail:
    """Регистрация: никнейм + e-mail + пароль + обязательные согласия.

    Ответ одинаков для нового и уже занятого e-mail (защита от перечисления аккаунтов):
    владельцу занятого адреса уходит письмо «у вас уже есть аккаунт». Занятый никнейм —
    публичная информация, о нём сообщаем явно (409).
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        hasher: PasswordHasher,
        limiter: RateLimiter,
        email: EmailSender,
        secrets: SecretGenerator,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._uow, self._hasher, self._limiter = uow, hasher, limiter
        self._email, self._secrets, self._clock, self._config = email, secrets, clock, config

    async def __call__(self, cmd: RegisterCommand) -> UUID | None:
        """Зарегистрировать. Возвращает id нового пользователя (``None`` — e-mail занят).

        Возвращаемое значение не должно попадать в HTTP-ответ: см. докстринг класса.
        """
        if cmd.ip:
            ensure_allowed(
                await self._limiter.hit(
                    rate_key("register:ip", cmd.ip),
                    limit=self._config.limits.register_per_ip_per_min,
                    window_s=60,
                )
            )
        nickname = Nickname.parse(cmd.nickname)
        email = EmailAddress.parse(cmd.email)
        display_name = DisplayName.parse(cmd.display_name or nickname.value)
        locale = _parse_locale(cmd.locale)
        self._config.password_policy.check(cmd.password, nickname=nickname.value, email=email.value)
        missing = missing_consents(self._config.required_consents, cmd.consents)
        if missing:
            raise InvalidInputError(
                "нужно принять обязательные согласия",
                code="auth.consents_required",
                details={"missing": [f"{c.kind}:{c.version}" for c in missing]},
            )
        # Хэшируем до проверки занятости: время ответа не зависит от существования e-mail.
        password_hash = await self._hasher.hash(cmd.password)

        async with self._uow() as uow:
            if await uow.users.find_by_nickname(nickname) is not None:
                raise ConflictError("никнейм занят", code="user.nickname_taken")
            if await uow.users.find_by_email(email) is not None:
                taken = True
            else:
                taken = False
                now = self._clock.now()
                user = User.register(
                    uuid7(),
                    nickname=nickname,
                    email=email,
                    display_name=display_name,
                    locale=locale,
                    roles=frozenset({DEFAULT_ROLE}),
                    now=now,
                )
                await uow.users.add(user)
                await uow.credentials.set_password_hash(user.id, password_hash, now)
                await uow.consents.add(
                    Consent(user.id, c.kind, c.version, now) for c in self._config.required_consents
                )
                secret = await _issue_email_token(
                    uow,
                    self._secrets,
                    self._clock,
                    user_id=user.id,
                    purpose=EmailTokenPurpose.VERIFY,
                    ttl_s=self._config.verify_ttl_s,
                )
                uow.track(user)
                await uow.commit()

        if taken:
            await _send_safely(self._email, EmailMessage(email, "already_registered", None))
            return None
        link = self._config.verify_link.format(token=secret)
        await _send_safely(self._email, EmailMessage(email, "verify", link))
        log.info("auth.user_registered", user_id=str(user.id))
        return user.id


class VerifyEmail:
    """Подтверждение e-mail по одноразовой ссылке; bootstrap-админ получает роль ``admin``."""

    def __init__(self, uow: UnitOfWorkFactory, clock: Clock, config: AuthConfig) -> None:
        self._uow, self._clock, self._config = uow, clock, config

    async def __call__(self, token: str) -> None:
        """Погасить токен и подтвердить адрес.

        Raises:
            InvalidInputError: ``auth.email_token_invalid`` — токен неверный, истёк или погашен.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            user_id = await uow.email_tokens.consume(
                hash_secret(token), EmailTokenPurpose.VERIFY, now
            )
            user = await uow.users.get(user_id) if user_id else None
            if user is None or not user.is_active:
                raise InvalidInputError("ссылка недействительна", code="auth.email_token_invalid")
            user.verify_email(now)
            if user.email.value in self._config.bootstrap_admin_emails:
                # Роль admin — только после подтверждения владения адресом (§4, настройки).
                matrix = await uow.roles.get_matrix()
                before = sorted(user.roles)
                if user.change_roles(
                    user.roles | {ADMIN_ROLE}, matrix, actor_id=None, reason="bootstrap_admin"
                ):
                    await uow.roles.add_audit(
                        RoleChange(
                            id=uuid7(),
                            kind="user_roles",
                            user_id=user.id,
                            actor_id=None,
                            before={"roles": before},
                            after={"roles": sorted(user.roles)},
                            reason="bootstrap_admin",
                            created_at=now,
                        )
                    )
                    log.warning("auth.bootstrap_admin_granted", user_id=str(user.id))
            await uow.users.save(user)
            uow.track(user)
            await uow.commit()


class PasswordAuthenticator:
    """Проверка логина (e-mail или никнейм) и пароля с лимитами и выравниванием времени."""

    def __init__(
        self,
        hasher: PasswordHasher,
        limiter: RateLimiter,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._hasher, self._limiter, self._config = hasher, limiter, config
        self._clock = clock

    async def authenticate(
        self, uow: IdentityUnitOfWork, login: str, password: str, ip: str | None
    ) -> User:
        """Найти пользователя и проверить пароль (rehash — в той же транзакции).

        Raises:
            RateLimitedError: превышен лимит по IP или по аккаунту.
            UnauthenticatedError: ``auth.invalid_credentials``.
            ForbiddenError: ``auth.email_not_verified``.
        """
        limits = self._config.limits
        if ip:
            ensure_allowed(
                await self._limiter.hit(
                    rate_key("login:ip", ip), limit=limits.login_per_ip_per_min, window_s=60
                )
            )
        account_key = rate_key("login:acct", login.strip())
        account_window = 15 * 60
        ensure_allowed(
            await self._limiter.peek(
                account_key, limit=limits.login_per_account_per_15min, window_s=account_window
            )
        )

        user = await self._find(uow, login)
        password_hash = (
            await uow.credentials.get_password_hash(user.id)
            if user is not None and user.is_active
            else None
        )
        if user is None or password_hash is None:
            await self._hasher.verify_dummy(password)
            ok = False
        else:
            ok = await self._hasher.verify(password_hash, password)
        if not ok or user is None or password_hash is None:
            await self._limiter.hit(
                account_key, limit=limits.login_per_account_per_15min, window_s=account_window
            )
            raise _invalid_credentials()
        if not user.email_verified:
            raise ForbiddenError("подтвердите e-mail", code="auth.email_not_verified")
        if self._hasher.needs_rehash(password_hash):
            await uow.credentials.set_password_hash(
                user.id, await self._hasher.hash(password), self._clock.now()
            )
        return user

    @staticmethod
    async def _find(uow: IdentityUnitOfWork, login: str) -> User | None:
        try:
            if "@" in login:
                return await uow.users.find_by_email(EmailAddress.parse(login))
            return await uow.users.find_by_nickname(Nickname.parse(login))
        except InvalidInputError:
            return None  # некорректный логин — как несуществующий, без подсказок


class LoginWithPassword:
    """Вход на сайт: логин = e-mail или никнейм (D2)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        authenticator: PasswordAuthenticator,
        sessions: SessionIssuer,
    ) -> None:
        self._uow, self._auth, self._sessions = uow, authenticator, sessions

    async def __call__(self, login: str, password: str, *, ip: str | None) -> IssuedSession:
        """Проверить пароль и открыть web-сессию."""
        async with self._uow() as uow:
            user = await self._auth.authenticate(uow, login, password, ip)
            matrix = await uow.roles.get_matrix()
            issued = await self._sessions.open(uow, user, matrix, kind=SessionKind.WEB, ip=ip)
            await uow.commit()
        log.info("auth.login", user_id=str(user.id), method="password")
        return issued


class AdminLogin:
    """Вход в админку: только паролем и только с правом ``admin.access`` (D2)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        authenticator: PasswordAuthenticator,
        sessions: SessionIssuer,
    ) -> None:
        self._uow, self._auth, self._sessions = uow, authenticator, sessions

    async def __call__(self, login: str, password: str, *, ip: str | None) -> IssuedSession:
        """Проверить пароль и право, открыть admin-сессию.

        Raises:
            ForbiddenError: ``auth.forbidden`` — нет права ``admin.access``.
        """
        async with self._uow() as uow:
            user = await self._auth.authenticate(uow, login, password, ip)
            matrix = await uow.roles.get_matrix()
            if ADMIN_ACCESS_PERM not in matrix.effective_perms(user.roles):
                raise ForbiddenError("нет доступа к админке", code="auth.forbidden")
            issued = await self._sessions.open(uow, user, matrix, kind=SessionKind.ADMIN, ip=ip)
            await uow.commit()
        log.info("auth.login", user_id=str(user.id), method="admin_password")
        return issued


class RefreshSession:
    """Ротация refresh-токена; повторное использование отзывает всю сессию."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        sessions: SessionIssuer,
        revocation: RevocationStore,
        clock: Clock,
    ) -> None:
        self._uow, self._sessions = uow, sessions
        self._revocation, self._clock = revocation, clock

    async def __call__(
        self, refresh_token: str, *, kind: SessionKind, required_perm: str | None = None
    ) -> IssuedSession:
        """Обменять refresh-токен на новую пару.

        Raises:
            UnauthenticatedError: ``auth.refresh_invalid`` / ``auth.refresh_reused`` /
                ``auth.refresh_expired`` / ``auth.session_revoked``.
            ForbiddenError: у admin-сессии больше нет ``required_perm``.
        """
        invalid = UnauthenticatedError(
            "недействительный refresh-токен", code="auth.refresh_invalid"
        )
        now = self._clock.now()
        reused: UUID | None = None
        async with self._uow() as uow:
            token = await uow.sessions.find_refresh_for_update(hash_secret(refresh_token))
            session = await uow.sessions.get(token.session_id) if token else None
            if token is None or session is None or session.kind is not kind:
                raise invalid
            secret, new_hash = self._sessions.new_refresh_secret()
            result = rotate_refresh_token(
                session,
                token,
                now=now,
                new_id=uuid7(),
                new_hash=new_hash,
                ttl=self._sessions.refresh_ttl,
            )
            if isinstance(result, ReuseDetected):
                await uow.sessions.save(session)
                await uow.sessions.save_refresh(token)
                await uow.commit()
                reused = session.id
            else:
                user = await uow.users.get(session.user_id)
                matrix = await uow.roles.get_matrix()
                if user is None or not user.is_active:
                    raise invalid
                if required_perm and required_perm not in matrix.effective_perms(user.roles):
                    raise ForbiddenError("нет доступа к админке", code="auth.forbidden")
                await uow.sessions.save_refresh(token)
                await uow.sessions.add_refresh(result.token)
                await uow.sessions.save(session)
                issued = self._sessions.access(user, matrix, session.id, refresh_secret=secret)
                await uow.commit()
        if reused is not None:
            await revoke_quietly(self._revocation, [reused])
            log.warning("auth.refresh_reuse_detected", session_id=str(reused))
            raise UnauthenticatedError(
                "refresh-токен уже использован, сессия завершена", code="auth.refresh_reused"
            )
        return issued


class Logout:
    """Выход из одной сессии."""

    def __init__(self, uow: UnitOfWorkFactory, revocation: RevocationStore, clock: Clock) -> None:
        self._uow, self._revocation, self._clock = uow, revocation, clock

    async def __call__(
        self, *, session_id: UUID | None = None, refresh_token: str | None = None
    ) -> None:
        """Отозвать сессию по ``sid`` (из access-токена) или по refresh-токену. Идемпотентно."""
        async with self._uow() as uow:
            if session_id is None and refresh_token:
                token = await uow.sessions.find_refresh_for_update(hash_secret(refresh_token))
                session_id = token.session_id if token else None
            session = await uow.sessions.get(session_id) if session_id else None
            if session is None or not session.revoke(self._clock.now(), "logout"):
                return
            await uow.sessions.save(session)
            await uow.commit()
        await revoke_quietly(self._revocation, [session.id])


async def revoke_all_sessions(
    uow: IdentityUnitOfWork, user_id: UUID, clock: Clock, reason: str
) -> list[UUID]:
    """Отозвать все активные сессии пользователя в транзакции ``uow``."""
    now = clock.now()
    revoked: list[UUID] = []
    for session in await uow.sessions.active_for_user(user_id):
        if session.revoke(now, reason):
            await uow.sessions.save(session)
            revoked.append(session.id)
    return revoked


class LogoutAll:
    """Выйти на всех устройствах: старые access-токены отвергаются по ``sid``."""

    def __init__(self, uow: UnitOfWorkFactory, revocation: RevocationStore, clock: Clock) -> None:
        self._uow, self._revocation, self._clock = uow, revocation, clock

    async def __call__(self, user_id: UUID) -> int:
        """Отозвать все сессии. Возвращает их количество."""
        async with self._uow() as uow:
            revoked = await revoke_all_sessions(uow, user_id, self._clock, "logout_all")
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        return len(revoked)


class RequestPasswordReset:
    """Запрос сброса пароля. Ответ всегда одинаковый — аккаунты не перечисляются."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        limiter: RateLimiter,
        email: EmailSender,
        secrets: SecretGenerator,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._uow, self._limiter, self._email = uow, limiter, email
        self._secrets, self._clock, self._config = secrets, clock, config

    async def __call__(self, email: str, *, ip: str | None) -> None:
        """Отправить ссылку сброса, если аккаунт существует."""
        if ip:
            ensure_allowed(
                await self._limiter.hit(
                    rate_key("reset:ip", ip),
                    limit=self._config.limits.reset_per_ip_per_min,
                    window_s=60,
                )
            )
        try:
            address = EmailAddress.parse(email)
        except InvalidInputError:
            return
        async with self._uow() as uow:
            user = await uow.users.find_by_email(address)
            if user is None or not user.is_active:
                return
            secret = await _issue_email_token(
                uow,
                self._secrets,
                self._clock,
                user_id=user.id,
                purpose=EmailTokenPurpose.RESET,
                ttl_s=self._config.reset_ttl_s,
            )
            await uow.commit()
        link = self._config.reset_link.format(token=secret)
        await _send_safely(self._email, EmailMessage(address, "reset", link))


class ResetPassword:
    """Новый пароль по ссылке из письма; все сессии отзываются."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        hasher: PasswordHasher,
        revocation: RevocationStore,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._uow, self._hasher, self._revocation = uow, hasher, revocation
        self._clock, self._config = clock, config

    async def __call__(self, token: str, new_password: str) -> None:
        """Сменить пароль.

        Raises:
            InvalidInputError: ``auth.email_token_invalid`` или ``auth.password_weak``.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            user_id = await uow.email_tokens.consume(
                hash_secret(token), EmailTokenPurpose.RESET, now
            )
            user = await uow.users.get(user_id) if user_id else None
            if user is None or not user.is_active:
                raise InvalidInputError("ссылка недействительна", code="auth.email_token_invalid")
            self._config.password_policy.check(
                new_password, nickname=user.nickname.value, email=user.email.value
            )
            await uow.credentials.set_password_hash(
                user.id, await self._hasher.hash(new_password), now
            )
            # Письмо пришло на адрес — владение подтверждено.
            if user.verify_email(now):
                await uow.users.save(user)
            revoked = await revoke_all_sessions(uow, user.id, self._clock, "password_reset")
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        log.info("auth.password_reset", user_id=str(user.id))
