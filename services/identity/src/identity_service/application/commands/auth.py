"""Регистрация, коды из писем, вход паролем, refresh, выход, сброс и смена пароля."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import (
    ConflictError,
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
    UnauthenticatedError,
)
from gv_core.ids import uuid7
from identity_service.application.commands.codes import (
    CodeChallenges,
    VerifiedSession,
    code_invalid,
    device_hash,
)
from identity_service.application.commands.guest_issuer import GuestSessionIssuer
from identity_service.application.config import AuthConfig, LimitConfig
from identity_service.application.dto import ChallengeIssued, RegisterCommand
from identity_service.application.ports import (
    ChallengePurpose,
    EmailChallenge,
    EmailMessage,
    EmailSender,
    IdentityUnitOfWork,
    PasswordHasher,
    RateLimiter,
    RevocationStore,
    RoleChange,
    UnitOfWorkFactory,
)
from identity_service.application.security import (
    NO_IP,
    canonical_login,
    email_subject_hash,
    ensure_allowed,
    hash_secret,
    ip_subnet,
    mail_key,
    rate_key,
    user_subject_hash,
)
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


async def send_safely(sender: EmailSender, message: EmailMessage) -> None:
    """Письмо после коммита: сбой отправки не откатывает операцию, но виден в логах."""
    try:
        await sender.send(message)
    except Exception as exc:
        log.warning("email.send_failed", purpose=message.purpose, error=type(exc).__name__)


async def mail_allowed_key(limiter: RateLimiter, limits: LimitConfig, key: str) -> bool:
    """Засчитать письмо по ключу адреса (``security.mail_key``); ``False`` — лимит исчерпан."""
    decision = await limiter.hit(
        rate_key("mail:addr", key),
        limit=limits.email_per_address,
        window_s=limits.email_address_window_s,
    )
    if not decision.allowed:
        log.info("email.suppressed_rate_limited")
    return decision.allowed


async def mail_allowed(limiter: RateLimiter, limits: LimitConfig, to: EmailAddress) -> bool:
    """Засчитать письмо на адрес (ключ — хэш адреса) и решить, можно ли его отправить.

    Лимит защищает владельца адреса от рассылки через register/forgot. Превышение не видно
    HTTP-клиенту (иначе раскрылось бы существование аккаунта) — письмо просто не уходит.
    """
    return await mail_allowed_key(limiter, limits, mail_key(to.value))


async def send_limited(
    sender: EmailSender, limiter: RateLimiter, limits: LimitConfig, message: EmailMessage
) -> None:
    """Письмо с лимитом на адрес (см. ``mail_allowed``)."""
    if await mail_allowed(limiter, limits, message.to):
        await send_safely(sender, message)


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
    """Регистрация: никнейм + e-mail + пароль + обязательные согласия, затем код из письма.

    Ответ одинаков по форме для нового и уже занятого e-mail (защита от перечисления
    аккаунтов): для занятого создаётся «пустышка», письмо уходит владельцу адреса
    («у вас уже есть аккаунт»). Если письмо подавлено лимитом на адрес, код не создаётся, а
    ответ остаётся прежней формы с фиктивным ``challenge_id``. Занятый никнейм — публичная
    информация, о нём сообщаем явно (409).
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        hasher: PasswordHasher,
        limiter: RateLimiter,
        email: EmailSender,
        codes: CodeChallenges,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._uow, self._hasher, self._limiter = uow, hasher, limiter
        self._email, self._codes, self._clock, self._config = email, codes, clock, config

    async def __call__(self, cmd: RegisterCommand) -> ChallengeIssued:
        """Зарегистрировать и выдать код подтверждения.

        Возвращает данные для ``ChallengeResponse``; факт существования адреса не раскрывает.
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
        purpose = ChallengePurpose.REGISTER
        message: EmailMessage | None = None
        new_user_id: UUID | None = None
        # Привязка к браузеру: cookie gv_bind ставится во всех ветках одинаково.
        binding_token, binding_hash = self._codes.new_binding()

        async with self._uow() as uow:
            if await uow.users.find_by_nickname(nickname) is not None:
                raise ConflictError("никнейм занят", code="user.nickname_taken")
            taken = await uow.users.find_by_email(email) is not None
            # Лимит на адрес считается одинаково для занятого и свободного адреса.
            allowed = await mail_allowed(self._limiter, self._config.limits, email)
            if taken:
                if allowed:
                    response = await self._codes.issue_decoy(
                        uow, purpose, email.value, binding=(binding_token, binding_hash)
                    )
                    message = EmailMessage(email, "already_registered", None)
                else:
                    response = self._codes.fake(purpose, binding_token=binding_token)
            else:
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
                new_user_id = user.id
                await uow.users.add(user)
                await uow.credentials.set_password_hash(user.id, password_hash, now)
                await uow.consents.add(
                    Consent(user.id, c.kind, c.version, now) for c in self._config.required_consents
                )
                if allowed:
                    challenge, code = await self._codes.issue(
                        uow,
                        purpose=purpose,
                        subject_hash=email_subject_hash(email.value),
                        user_id=user.id,
                        device_hash=binding_hash,
                        mail_key=mail_key(email.value),
                    )
                    response = self._codes.view(challenge, binding_token=binding_token)
                    message = EmailMessage(
                        email, f"code_{purpose.value}", None, code=code, challenge_id=challenge.id
                    )
                else:
                    response = self._codes.fake(purpose, binding_token=binding_token)
                uow.track(user)
            await uow.commit()

        if message is not None:
            await send_safely(self._email, message)
        if new_user_id is not None:
            log.info("auth.user_registered", user_id=str(new_user_id))
        return response


class VerifyEmail:
    """Подтверждение e-mail кодом: сразу открывает сессию и доверяет устройству.

    Bootstrap-админ получает роль ``admin`` только после подтверждения владения адресом.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        config: AuthConfig,
        *,
        codes: CodeChallenges,
        sessions: SessionIssuer,
    ) -> None:
        self._uow, self._clock, self._config = uow, clock, config
        self._codes, self._sessions = codes, sessions

    async def __call__(
        self,
        challenge_id: UUID,
        code: str,
        *,
        ip: str | None = None,
        binding_token: str | None = None,
    ) -> VerifiedSession:
        """Проверить код, подтвердить адрес и открыть web-сессию.

        ``binding_token`` — cookie ``gv_bind`` браузера, запросившего код; из другого браузера
        код считается неверным.

        Raises:
            InvalidInputError: ``auth.code_invalid``.
            GoneError: ``auth.code_expired``.
            RateLimitedError: лимит IP или ``auth.code_limit_exceeded``.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            challenge = await self._codes.check(
                uow, challenge_id, code, ChallengePurpose.REGISTER, ip
            )
            self._codes.require_binding(challenge, binding_token)
            user = await uow.users.get(challenge.user_id) if challenge.user_id else None
            if user is None or not user.is_active:
                raise code_invalid(0)
            await self._codes.consume(uow, challenge)
            user.verify_email(now)
            matrix = await uow.roles.get_matrix()
            if user.email.value in self._config.bootstrap_admin_emails:
                # Роль admin — только после подтверждения владения адресом (§4, настройки).
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
            token = await self._codes.trust_device(uow, user.id)
            issued = await self._sessions.open(uow, user, matrix, kind=SessionKind.WEB, ip=ip)
            await uow.commit()
        log.info("auth.email_verified", user_id=str(user.id))
        return VerifiedSession(issued, token)


class PasswordAuthenticator:
    """Проверка пароля с лимитами и выравниванием времени: вход, admin-вход и повторная
    аутентификация (привязка/отвязка сервисов, удаление аккаунта).

    Лимиты (значения — ``LimitConfig``), каждая попытка засчитывается атомарно ДО проверки
    пароля, поэтому параллельные запросы не проскакивают лимит:

    * по IP клиента;
    * по аккаунту из подсети клиента (/24, /64) — чужие неудачи из другой сети не
      блокируют владельца;
    * общий мягкий лимит по аккаунту — против распределённого перебора.

    Аккаунт в ключе — ``user.id`` найденного пользователя (все варианты логина: e-mail,
    ник, Unicode-двойники — один счётчик); для несуществующего — канонический логин.
    Успешная проверка не наказывается: счётчик подсети обнуляется, общий — прощает попытку.
    """

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
        self,
        uow: IdentityUnitOfWork,
        login: str,
        password: str,
        ip: str | None,
        *,
        allow_unverified: bool = False,
    ) -> User:
        """Найти пользователя и проверить пароль (rehash — в той же транзакции).

        ``allow_unverified`` — вход на сайт сам выдаёт код подтверждения неподтверждённому
        пользователю (ADR 0041); остальные вызовы по-прежнему отказывают.

        Raises:
            RateLimitedError: превышен лимит по IP или по аккаунту.
            UnauthenticatedError: ``auth.invalid_credentials``.
            ForbiddenError: ``auth.email_not_verified``.
        """
        await self._limit_ip(ip)
        user = await self._find(uow, login)
        account = f"user:{user.id}" if user is not None else f"login:{canonical_login(login)}"
        keys = await self._limit_account(account, ip)
        password_hash = (
            await uow.credentials.get_password_hash(user.id)
            if user is not None and user.is_active
            else None
        )
        if user is None or password_hash is None:
            await self._hasher.verify_dummy(password)
            raise _invalid_credentials()
        if not await self._hasher.verify(password_hash, password):
            raise _invalid_credentials()
        await self._forgive(keys)
        if not user.email_verified and not allow_unverified:
            raise ForbiddenError("подтвердите e-mail", code="auth.email_not_verified")
        await self._rehash(uow, user, password_hash, password)
        return user

    async def confirm_password(
        self, uow: IdentityUnitOfWork, user: User, password: str | None, ip: str | None
    ) -> None:
        """Повторная аутентификация вошедшего пользователя перед чувствительным действием.

        Те же лимиты и счётчики, что у входа: подбор пароля через эти ручки невозможен.

        Raises:
            InvalidInputError: ``auth.password_required`` — пароль не передан.
            RateLimitedError: превышен лимит.
            UnauthenticatedError: ``auth.invalid_credentials`` — неверный пароль.
        """
        if not password:
            raise InvalidInputError("подтвердите действие паролем", code="auth.password_required")
        await self._limit_ip(ip)
        keys = await self._limit_account(f"user:{user.id}", ip)
        password_hash = await uow.credentials.get_password_hash(user.id)
        if password_hash is None:
            await self._hasher.verify_dummy(password)
            raise _invalid_credentials()
        if not await self._hasher.verify(password_hash, password):
            raise _invalid_credentials()
        await self._forgive(keys)
        await self._rehash(uow, user, password_hash, password)

    async def _limit_ip(self, ip: str | None) -> None:
        if not ip:
            return
        limits = self._config.limits
        ensure_allowed(
            await self._limiter.hit(
                rate_key("login:ip", ip),
                limit=limits.login_per_ip,
                window_s=limits.login_ip_window_s,
            )
        )

    async def _limit_account(self, account: str, ip: str | None) -> tuple[str, str]:
        limits = self._config.limits
        subnet = (
            ip_subnet(ip, v4_bits=limits.subnet_v4_bits, v6_bits=limits.subnet_v6_bits) or NO_IP
        )
        subnet_key = rate_key("login:acct_net", f"{account}|{subnet}")
        total_key = rate_key("login:acct", account)
        window = limits.login_account_window_s
        ensure_allowed(
            await self._limiter.hit(
                subnet_key, limit=limits.login_per_account_subnet, window_s=window
            )
        )
        ensure_allowed(
            await self._limiter.hit(total_key, limit=limits.login_per_account, window_s=window)
        )
        return subnet_key, total_key

    async def _forgive(self, keys: tuple[str, str]) -> None:
        subnet_key, total_key = keys
        await self._limiter.reset(subnet_key)
        await self._limiter.forgive(total_key)

    async def _rehash(
        self, uow: IdentityUnitOfWork, user: User, password_hash: str, password: str
    ) -> None:
        if self._hasher.needs_rehash(password_hash):
            await uow.credentials.set_password_hash(
                user.id, await self._hasher.hash(password), self._clock.now()
            )

    @staticmethod
    async def _find(uow: IdentityUnitOfWork, login: str) -> User | None:
        try:
            if "@" in login:
                return await uow.users.find_by_email(EmailAddress.parse(login))
            return await uow.users.find_by_nickname(Nickname.parse(login))
        except InvalidInputError:
            return None  # некорректный логин — как несуществующий, без подсказок


@dataclass(frozen=True, slots=True)
class LoginResult:
    """Итог входа паролем: сессия (известное устройство) либо код с почты (новое).

    Attributes:
        session: Выданная сессия или ``None``.
        challenge: Данные кода для ``login/confirm`` или ``None``.
    """

    session: IssuedSession | None = None
    challenge: ChallengeIssued | None = None

    @property
    def status(self) -> str:
        """``authenticated`` или ``code_required``."""
        return "authenticated" if self.session is not None else "code_required"


class LoginWithPassword:
    """Вход на сайт: логин = e-mail или никнейм (D2); с нового устройства нужен код (ADR 0041)."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        authenticator: PasswordAuthenticator,
        sessions: SessionIssuer,
        *,
        codes: CodeChallenges,
        limiter: RateLimiter,
        email: EmailSender,
        config: AuthConfig,
    ) -> None:
        self._uow, self._auth, self._sessions = uow, authenticator, sessions
        self._codes, self._limiter, self._email, self._config = codes, limiter, email, config

    async def __call__(
        self, login: str, password: str, *, ip: str | None, device_token: str | None = None
    ) -> LoginResult:
        """Проверить пароль; доверенное устройство получает сессию, остальные — код на почту.

        Сессия для неизвестного устройства не создаётся до ``ConfirmLogin``. Если письмо
        подавлено лимитом на адрес, ответ той же формы, но кода нет (фиктивный id).
        """
        message: EmailMessage | None = None
        async with self._uow() as uow:
            user = await self._auth.authenticate(uow, login, password, ip, allow_unverified=True)
            if not user.email_verified:
                # Письмо регистрации могло быть подавлено лимитом или потеряно: пароль верен,
                # поэтому выдаём новый код подтверждения (его вводят через verify-email).
                return await self._unverified(uow, user, ip)
            if await self._codes.is_trusted(uow, user.id, device_token):
                matrix = await uow.roles.get_matrix()
                issued = await self._sessions.open(uow, user, matrix, kind=SessionKind.WEB, ip=ip)
                await uow.commit()
                log.info("auth.login", user_id=str(user.id), method="password")
                return LoginResult(session=issued)
            await self._codes.limit_requests(ip)
            purpose = ChallengePurpose.LOGIN
            binding_token, binding_hash = self._codes.new_binding()
            if await mail_allowed(self._limiter, self._config.limits, user.email):
                challenge, code = await self._codes.issue(
                    uow,
                    purpose=purpose,
                    subject_hash=user_subject_hash(user.id),
                    user_id=user.id,
                    device_hash=binding_hash,
                    mail_key=mail_key(user.email.value),
                )
                response = self._codes.view(challenge, binding_token=binding_token)
                message = EmailMessage(
                    user.email, f"code_{purpose.value}", None, code=code, challenge_id=challenge.id
                )
            else:
                response = self._codes.fake(purpose, binding_token=binding_token)
            await uow.commit()
        if message is not None:
            await send_safely(self._email, message)
        log.info("auth.login_code_required", user_id=str(user.id))
        return LoginResult(challenge=response)

    async def _unverified(self, uow: IdentityUnitOfWork, user: User, ip: str | None) -> LoginResult:
        """Код подтверждения e-mail неподтверждённому пользователю (subject общий с register)."""
        purpose = ChallengePurpose.REGISTER
        await self._codes.limit_requests(ip)
        message: EmailMessage | None = None
        binding_token, binding_hash = self._codes.new_binding()
        if await mail_allowed(self._limiter, self._config.limits, user.email):
            challenge, code = await self._codes.issue(
                uow,
                purpose=purpose,
                subject_hash=email_subject_hash(user.email.value),
                user_id=user.id,
                device_hash=binding_hash,
                mail_key=mail_key(user.email.value),
            )
            response = self._codes.view(challenge, binding_token=binding_token)
            message = EmailMessage(
                user.email, f"code_{purpose.value}", None, code=code, challenge_id=challenge.id
            )
        else:
            response = self._codes.fake(purpose, binding_token=binding_token)
        await uow.commit()
        if message is not None:
            await send_safely(self._email, message)
        log.info("auth.verification_code_reissued", user_id=str(user.id))
        return LoginResult(challenge=response)


class ConfirmLogin:
    """Подтверждение входа с нового устройства кодом: сессия + доверие устройству."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        codes: CodeChallenges,
        sessions: SessionIssuer,
    ) -> None:
        self._uow, self._codes, self._sessions = uow, codes, sessions

    async def __call__(
        self,
        challenge_id: UUID,
        code: str,
        *,
        ip: str | None = None,
        binding_token: str | None = None,
    ) -> VerifiedSession:
        """Проверить код, открыть web-сессию и доверить устройство.

        Raises:
            InvalidInputError: ``auth.code_invalid``.
            GoneError: ``auth.code_expired``.
            RateLimitedError: лимит IP или ``auth.code_limit_exceeded``.
        """
        async with self._uow() as uow:
            challenge = await self._codes.check(uow, challenge_id, code, ChallengePurpose.LOGIN, ip)
            self._codes.require_binding(challenge, binding_token)
            user = await uow.users.get(challenge.user_id) if challenge.user_id else None
            if user is None or not user.is_active or not user.email_verified:
                raise code_invalid(0)
            await self._codes.consume(uow, challenge)
            token = await self._codes.trust_device(uow, user.id)
            matrix = await uow.roles.get_matrix()
            issued = await self._sessions.open(uow, user, matrix, kind=SessionKind.WEB, ip=ip)
            await uow.commit()
        log.info("auth.login", user_id=str(user.id), method="password_code")
        return VerifiedSession(issued, token)


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
    """Ротация refresh-токена; повторное использование отзывает всю сессию.

    Гостевая сессия ротируется так же, но новый access-токен выпускает ``guests`` (без
    прав, ``gst = true``); без ``guests`` гостевой refresh-токен недействителен.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        sessions: SessionIssuer,
        revocation: RevocationStore,
        clock: Clock,
        *,
        guests: GuestSessionIssuer | None = None,
    ) -> None:
        self._uow, self._sessions = uow, sessions
        self._revocation, self._clock = revocation, clock
        self._guests = guests

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
            guests = self._guests
            if session.is_guest and (guests is None or required_perm):
                raise invalid  # у гостя нет прав и admin-сессий
            secret, new_hash = self._sessions.new_refresh_secret()
            result = rotate_refresh_token(
                session,
                token,
                now=now,
                new_id=uuid7(),
                new_hash=new_hash,
                ttl=guests.refresh_ttl
                if session.is_guest and guests is not None
                else self._sessions.refresh_ttl,
            )
            if isinstance(result, ReuseDetected):
                await uow.sessions.save(session)
                await uow.sessions.save_refresh(token)
                await uow.commit()
                reused = session.id
            elif session.is_guest and guests is not None:
                await uow.sessions.save_refresh(token)
                await uow.sessions.add_refresh(result.token)
                await uow.sessions.save(session)
                issued = guests.access(session, refresh_secret=secret)
                await uow.commit()
            else:
                user = await uow.users.get(session.user_id) if session.user_id else None
                matrix = await uow.roles.get_matrix()
                if user is None or not user.is_active:
                    raise invalid
                if required_perm and required_perm not in matrix.effective_perms(user.roles):
                    raise ForbiddenError("нет доступа к админке", code="auth.forbidden")
                mask = await self._sessions.sanctions_mask(uow, user.id)  # бан - 403
                await uow.sessions.save_refresh(token)
                await uow.sessions.add_refresh(result.token)
                await uow.sessions.save(session)
                issued = self._sessions.access(
                    user, matrix, session.id, refresh_secret=secret, sanctions=mask
                )
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
        self,
        *,
        session_id: UUID | None = None,
        refresh_token: str | None = None,
        kind: SessionKind | None = None,
    ) -> None:
        """Отозвать сессию по ``sid`` (из access-токена) или по refresh-токену. Идемпотентно.

        ``kind`` — отзывать только сессию этого вида (выход из админки не трогает
        web-сессии, даже если ему передали чужой refresh-токен).
        """
        async with self._uow() as uow:
            if session_id is None and refresh_token:
                token = await uow.sessions.find_refresh_for_update(hash_secret(refresh_token))
                session_id = token.session_id if token else None
            session = await uow.sessions.get(session_id) if session_id else None
            if session is None or (kind is not None and session.kind is not kind):
                return
            if not session.revoke(self._clock.now(), "logout"):
                return
            await uow.sessions.save(session)
            await uow.commit()
        await revoke_quietly(self._revocation, [session.id])


async def revoke_all_sessions(
    uow: IdentityUnitOfWork,
    user_id: UUID,
    clock: Clock,
    reason: str,
    *,
    except_session_id: UUID | None = None,
) -> list[UUID]:
    """Отозвать активные сессии пользователя в транзакции ``uow`` (кроме ``except_session_id``)."""
    now = clock.now()
    revoked: list[UUID] = []
    for session in await uow.sessions.active_for_user(user_id):
        if session.id != except_session_id and session.revoke(now, reason):
            await uow.sessions.save(session)
            revoked.append(session.id)
    return revoked


class LogoutAll:
    """Выйти на всех устройствах: старые access-токены отвергаются по ``sid``.

    Доверие снимается со всех устройств, кроме текущего (ADR 0041): владелец, нажавший
    «выйти везде», не должен снова вводить код на своём устройстве.
    """

    def __init__(self, uow: UnitOfWorkFactory, revocation: RevocationStore, clock: Clock) -> None:
        self._uow, self._revocation, self._clock = uow, revocation, clock

    async def __call__(self, user_id: UUID, *, device_token: str | None = None) -> int:
        """Отозвать все сессии. Возвращает их количество."""
        async with self._uow() as uow:
            revoked = await revoke_all_sessions(uow, user_id, self._clock, "logout_all")
            await uow.trusted_devices.revoke_for_user(
                user_id, self._clock.now(), except_hash=device_hash(device_token)
            )
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        return len(revoked)


class RequestPasswordReset:
    """Запрос сброса пароля кодом. Ответ одинаков для любого адреса — аккаунты не перечисляются."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        limiter: RateLimiter,
        email: EmailSender,
        codes: CodeChallenges,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._uow, self._limiter, self._email = uow, limiter, email
        self._codes, self._clock, self._config = codes, clock, config

    async def __call__(self, email: str, *, ip: str | None) -> ChallengeIssued:
        """Выдать код сброса, если аккаунт существует; иначе — «пустышка» той же формы."""
        purpose = ChallengePurpose.RESET
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
            return self._codes.fake(purpose)
        message: EmailMessage | None = None
        async with self._uow() as uow:
            user = await uow.users.find_by_email(address)
            # Лимит — до выдачи кода и одинаково для любого адреса: иначе поток запросов
            # гасил бы код, уже полученный владельцем, не отправляя ему новый.
            if not await mail_allowed(self._limiter, self._config.limits, address):
                return self._codes.fake(purpose)
            if user is None or not user.is_active:
                response = await self._codes.issue_decoy(uow, purpose, address.value)
            else:
                challenge, code = await self._codes.issue(
                    uow,
                    purpose=purpose,
                    subject_hash=email_subject_hash(address.value),
                    user_id=user.id,
                    mail_key=mail_key(address.value),
                )
                response = self._codes.view(challenge)
                message = EmailMessage(
                    address, f"code_{purpose.value}", None, code=code, challenge_id=challenge.id
                )
            await uow.commit()
        if message is not None:
            await send_safely(self._email, message)
        return response


class ResetPassword:
    """Новый пароль по коду из письма; владелец сразу входит и доверяет это устройство.

    Все прежние сессии и все доверенные устройства отзываются, а текущее устройство —
    единственное доверенное: код из письма уже подтвердил владение адресом, повторный ввод
    пароля и ещё одного кода ничего не добавил бы. Привязанные внешние аккаунты не
    удаляются (они могут быть единственным способом входа владельца), но письмо о смене
    пароля перечисляет их — владелец увидит чужую привязку и отвяжет её.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        hasher: PasswordHasher,
        revocation: RevocationStore,
        clock: Clock,
        config: AuthConfig,
        *,
        email: EmailSender,
        codes: CodeChallenges,
        sessions: SessionIssuer,
    ) -> None:
        self._uow, self._hasher, self._revocation = uow, hasher, revocation
        self._clock, self._config, self._email, self._codes = clock, config, email, codes
        self._sessions = sessions

    async def __call__(
        self,
        challenge_id: UUID,
        code: str,
        new_password: str,
        *,
        ip: str | None = None,
    ) -> VerifiedSession:
        """Сменить пароль, открыть web-сессию и доверить устройство.

        Raises:
            InvalidInputError: ``auth.code_invalid`` или ``auth.password_weak``.
            GoneError: ``auth.code_expired``.
            RateLimitedError: лимит IP или ``auth.code_limit_exceeded``.
        """
        # Правила, не зависящие от пользователя, — до списания попытки: слабый пароль не
        # «сжигает» код, а ответ одинаков для настоящего кода и пустышки.
        self._config.password_policy.check(new_password, nickname="", email="")
        now = self._clock.now()
        async with self._uow() as uow:
            challenge = await self._codes.check(uow, challenge_id, code, ChallengePurpose.RESET, ip)
            user = await uow.users.get(challenge.user_id) if challenge.user_id else None
            if user is None or not user.is_active:
                raise code_invalid(0)
            self._config.password_policy.check(
                new_password, nickname=user.nickname.value, email=user.email.value
            )
            await self._codes.consume(uow, challenge)
            await uow.credentials.set_password_hash(
                user.id, await self._hasher.hash(new_password), now
            )
            # Код пришёл на адрес — владение подтверждено.
            if user.verify_email(now):
                await uow.users.save(user)
            revoked = await revoke_all_sessions(uow, user.id, self._clock, "password_reset")
            await uow.trusted_devices.revoke_for_user(user.id, now)
            # Новая сессия и доверие — после отзыва прежних, иначе они отозвались бы сами.
            token = await self._codes.trust_device(uow, user.id)
            matrix = await uow.roles.get_matrix()
            issued = await self._sessions.open(uow, user, matrix, kind=SessionKind.WEB, ip=ip)
            providers = tuple(
                sorted(i.provider.value for i in await uow.identities.list_for_user(user.id))
            )
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        # Уведомление о безопасности — без лимита на адрес: его вызывает владелец кода.
        await send_safely(
            self._email, EmailMessage(user.email, "password_changed", None, providers)
        )
        log.info("auth.password_reset", user_id=str(user.id), sessions_revoked=len(revoked))
        return VerifiedSession(issued, token)


class ResendCode:
    """Повторная отправка кода: не раньше ``resend_after_s``, код заменяется, попытки сброшены."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        *,
        codes: CodeChallenges,
        limiter: RateLimiter,
        email: EmailSender,
        config: AuthConfig,
    ) -> None:
        self._uow, self._codes, self._limiter = uow, codes, limiter
        self._email, self._config = email, config

    async def __call__(self, challenge_id: UUID, *, ip: str | None = None) -> ChallengeIssued:
        """Отправить новый код по той же записи.

        Истёкший код заменяется новой записью (в ответе новый ``challenge_id``). Для
        «пустышки» письмо не уходит. Письмо подчиняется лимиту на адрес.

        Raises:
            InvalidInputError: ``auth.code_invalid`` — записи нет, код погашен или отменён.
            RateLimitedError: ``auth.code_resend_too_early`` (``retry_after``) или лимит IP.
        """
        await self._codes.limit_requests(ip)
        message: EmailMessage | None = None
        async with self._uow() as uow:
            current = await uow.email_challenges.get(challenge_id)
            if current is None:
                raise code_invalid(0)
            updated, code = await self._codes.resend(uow, current, send_allowed=self._send_allowed)
            user = await uow.users.get(updated.user_id) if updated.user_id else None
            # Код ``None`` — лимит писем исчерпан: запись не менялась, письма нет. Лимит
            # одинаков у настоящего кода и пустышки (ключ лежит в записи, не зависит от user).
            if code is not None and user is not None and user.is_active:
                message = EmailMessage(
                    user.email,
                    f"code_{updated.purpose.value}",
                    None,
                    code=code,
                    challenge_id=updated.id,
                )
            await uow.commit()
        if message is not None:
            await send_safely(self._email, message)
        return self._codes.view(updated)

    async def _send_allowed(self, challenge: EmailChallenge) -> bool:
        key = challenge.mail_key
        if key is None:  # записи без ключа (до миграции 0012) лимитом не охвачены
            return True
        return await mail_allowed_key(self._limiter, self._config.limits, key)


class RequestPasswordChange:
    """Смена пароля в профиле, шаг 1: текущий пароль (повторная аутентификация) → код на почту."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        authenticator: PasswordAuthenticator,
        *,
        codes: CodeChallenges,
        limiter: RateLimiter,
        email: EmailSender,
        config: AuthConfig,
    ) -> None:
        self._uow, self._auth, self._codes = uow, authenticator, codes
        self._limiter, self._email, self._config = limiter, email, config

    async def __call__(
        self,
        user_id: UUID,
        current_password: str | None,
        *,
        session_id: UUID | None,
        ip: str | None,
    ) -> ChallengeIssued:
        """Проверить текущий пароль и выдать код.

        Raises:
            InvalidInputError: ``auth.password_required``.
            UnauthenticatedError: ``auth.invalid_credentials``.
            RateLimitedError: лимит проверки пароля или запросов кода.
            NotFoundError: аккаунта нет.
        """
        await self._codes.limit_requests(ip)
        purpose = ChallengePurpose.PASSWORD_CHANGE
        message: EmailMessage | None = None
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            if user is None or not user.is_active:
                raise NotFoundError("аккаунт не найден", code="user.not_found")
            await self._auth.confirm_password(uow, user, current_password, ip)
            if await mail_allowed(self._limiter, self._config.limits, user.email):
                challenge, code = await self._codes.issue(
                    uow,
                    purpose=purpose,
                    subject_hash=user_subject_hash(user.id),
                    user_id=user.id,
                    mail_key=mail_key(user.email.value),
                    session_id=session_id,
                )
                response = self._codes.view(challenge)
                message = EmailMessage(
                    user.email, f"code_{purpose.value}", None, code=code, challenge_id=challenge.id
                )
            else:
                response = self._codes.fake(purpose)
            await uow.commit()
        if message is not None:
            await send_safely(self._email, message)
        return response


class ConfirmPasswordChange:
    """Смена пароля в профиле, шаг 2: код + новый пароль.

    Отзываются все сессии, кроме текущей, и доверие всех устройств, кроме текущего.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        hasher: PasswordHasher,
        revocation: RevocationStore,
        clock: Clock,
        config: AuthConfig,
        *,
        email: EmailSender,
        codes: CodeChallenges,
    ) -> None:
        self._uow, self._hasher, self._revocation = uow, hasher, revocation
        self._clock, self._config, self._email, self._codes = clock, config, email, codes

    async def __call__(
        self,
        user_id: UUID,
        challenge_id: UUID,
        code: str,
        new_password: str,
        *,
        session_id: UUID | None,
        ip: str | None = None,
    ) -> str:
        """Сменить пароль; текущее устройство остаётся (или становится) доверенным.

        Returns:
            Значение cookie ``gv_device`` (пришедшее или новое).

        Raises:
            InvalidInputError: ``auth.code_invalid`` (в том числе код чужого пользователя)
                или ``auth.password_weak``.
            GoneError: ``auth.code_expired``.
            RateLimitedError: лимит IP или ``auth.code_limit_exceeded``.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            # До списания попытки: чужой/неподходящий код и слабый пароль её не сжигают.
            user = await uow.users.get(user_id)
            known = await uow.email_challenges.get(challenge_id)
            if (
                user is None
                or not user.is_active
                or known is None
                or known.user_id != user_id
                or known.purpose is not ChallengePurpose.PASSWORD_CHANGE
            ):
                raise code_invalid(0)
            self._config.password_policy.check(
                new_password, nickname=user.nickname.value, email=user.email.value
            )
            challenge = await self._codes.check(
                uow, challenge_id, code, ChallengePurpose.PASSWORD_CHANGE, ip
            )
            await self._codes.consume(uow, challenge)
            await uow.credentials.set_password_hash(
                user.id, await self._hasher.hash(new_password), now
            )
            revoked = await revoke_all_sessions(
                uow, user.id, self._clock, "password_change", except_session_id=session_id
            )
            # Текущее устройство получает новый токен, прежние (в том числе его) снимаются.
            await uow.trusted_devices.revoke_for_user(user.id, now)
            token = await self._codes.trust_device(uow, user.id)
            providers = tuple(
                sorted(i.provider.value for i in await uow.identities.list_for_user(user.id))
            )
            await uow.commit()
        await revoke_quietly(self._revocation, revoked)
        await send_safely(
            self._email, EmailMessage(user.email, "password_changed", None, providers)
        )
        log.info("auth.password_changed", user_id=str(user.id), sessions_revoked=len(revoked))
        return token
