"""Composition root identity: адаптеры → use-cases → API (DI без фреймворка, ADR 0011).

Модуль вне слоёв (как ``main``): только он знает и инфраструктуру, и API.
"""

from __future__ import annotations

import secrets as stdlib_secrets
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import structlog
from prometheus_client import Counter

from gv_auth import Authenticator, JwtVerifier, RedisRevocationChecker, ServiceAuthenticator
from gv_core.errors import DependencyUnavailableError
from gv_email import BackgroundEmailSender, LogEmailSender, SmtpEmailSender
from gv_service import Environment, Infrastructure, PeriodicJob
from identity_service.api.common import ApiDeps
from identity_service.application.code_service import MIN_KEY_BYTES, HmacCodeService
from identity_service.application.commands.account import DeleteAccount, GrantConsents
from identity_service.application.commands.auth import (
    AdminLogin,
    ConfirmLogin,
    ConfirmPasswordChange,
    LoginWithPassword,
    Logout,
    LogoutAll,
    PasswordAuthenticator,
    RefreshSession,
    RegisterWithEmail,
    RequestPasswordChange,
    RequestPasswordReset,
    ResendCode,
    ResetPassword,
    VerifyEmail,
)
from identity_service.application.commands.bootstrap import EnsureBootstrapAdmin
from identity_service.application.commands.challenge_purge import PurgeEmailChallenges
from identity_service.application.commands.codes import CodeChallenges
from identity_service.application.commands.guest import (
    EndGuestSession,
    GuestUseCases,
    PurgeGuestSessions,
    StartGuestSession,
)
from identity_service.application.commands.guest_issuer import GuestConfig, GuestSessionIssuer
from identity_service.application.commands.oauth import (
    CompleteOAuth,
    LinkExternalAccount,
    LoginWithLinkedAccount,
    StartOAuth,
    UnlinkExternalAccount,
)
from identity_service.application.commands.realtime import (
    IssueRealtimeToken,
    RealtimeTokenConfig,
)
from identity_service.application.commands.roles import AssignRoles, UpdateRoleMatrix
from identity_service.application.commands.service_tokens import (
    IssueServiceToken,
    ServiceTokenConfig,
)
from identity_service.application.commands.staff_perms import ReannounceStaffPerms
from identity_service.application.config import AuthConfig, CodeConfig, LimitConfig
from identity_service.application.container import IdentityUseCases
from identity_service.application.ports import (
    EmailSender,
    IdentityUnitOfWork,
    OAuthProvider,
)
from identity_service.application.queries import (
    GetJwks,
    GetRoleMatrix,
    GetUser,
    ListLinkedAccounts,
    ListUsers,
)
from identity_service.application.queries.user_emails import ListVerifiedEmails, UserEmailsLimit
from identity_service.application.sessions import SessionIssuer
from identity_service.domain import (
    EmailAddress,
    PasswordPolicy,
    Provider,
    parse_consent_requirements,
)
from identity_service.domain.service_client import ServiceClient, ServiceClientRegistry
from identity_service.infrastructure.clients import (
    IdentityEmailSender,
    YandexOAuthProvider,
)
from identity_service.infrastructure.db.challenge_purger import SqlChallengePurger
from identity_service.infrastructure.db.guest_sessions import SqlGuestSessionPurger
from identity_service.infrastructure.db.repositories import (
    SqlChallengeAttempts,
    SqlIdentityUnitOfWork,
)
from identity_service.infrastructure.db.tables import TABLES
from identity_service.infrastructure.realtime import HmacRealtimeTokenSigner
from identity_service.infrastructure.redis_stores import (
    RedisOAuthStateStore,
    RedisRateLimiter,
    RedisRevocationStore,
    RedisStrictRateLimiter,
)
from identity_service.infrastructure.security import (
    Argon2Hasher,
    JwtIssuer,
    KeyConfigError,
    SigningKey,
    StaticKeyProvider,
    SystemSecrets,
    generate_dev_key,
    parse_signing_keys,
)
from identity_service.settings import Settings

log = structlog.get_logger(__name__)

REVOCATION_FAIL_OPEN = Counter(
    "identity_revocation_fail_open_total",
    "Проверки отзыва sid, пропущенные из-за недоступного Redis",
)
GUEST_SESSIONS_STARTED = Counter(
    "identity_guest_sessions_started_total", "Выданные гостевые сессии (D5 плана этапа 2)"
)


@cache
def common_passwords() -> frozenset[str]:
    """Топ-10k распространённых паролей (SecLists, нижний регистр)."""
    text = (
        resources.files("identity_service.infrastructure")
        .joinpath("data/common_passwords.txt")
        .read_text("utf-8")
    )
    return frozenset(line.strip().lower() for line in text.splitlines() if line.strip())


def auth_config(settings: Settings) -> AuthConfig:
    """Настройки → параметры use-cases."""
    return AuthConfig(
        access_ttl_s=settings.jwt.access_ttl_s,
        refresh_ttl_s=settings.jwt.refresh_ttl_s,
        oauth_state_ttl_s=settings.oauth.state_ttl_s,
        required_consents=parse_consent_requirements(settings.consents.required),
        bootstrap_admin_emails=frozenset(
            EmailAddress.parse(e).value for e in settings.bootstrap_admin_emails
        ),
        codes=code_config(settings),
        return_to_prefixes=settings.oauth.return_to_prefixes,
        password_policy=PasswordPolicy(banned=common_passwords()),
        limits=limit_config(settings),
    )


def code_config(settings: Settings) -> CodeConfig:
    """Коды из писем и доверенные устройства: настройки → параметры use-cases (ADR 0041)."""
    c = settings.email_code
    return CodeConfig(
        length=c.length,
        ttl_s=c.ttl_s,
        max_attempts=c.max_attempts,
        resend_after_s=c.resend_after_s,
        daily_fail_limit=c.daily_fail_limit,
        request_per_ip_per_min=c.request_per_ip_per_min,
        confirm_per_ip_per_min=c.confirm_per_ip_per_min,
        device_ttl_s=c.device_cookie_ttl_days * 86_400,
        max_lifetime_s=c.max_lifetime_s,
        check_concurrency=c.check_concurrency,
        purge_retention_s=c.purge_retention_s,
        device_revoked_retention_s=c.device_revoked_retention_s,
        purge_batch=c.purge_batch,
        purge_max_batches=c.purge_max_batches,
    )


def code_hmac_key(settings: Settings) -> bytes:
    """Ключ HMAC кодов: из ``GV_EMAIL_CODE__HMAC_KEY``; в local/ci без него — временный.

    Временный ключ живёт до перезапуска процесса: непогашенные коды перестают подходить
    (допустимо локально). Вне local/ci пустой ключ отвергает ``check_email_codes_config``.

    Raises:
        KeyConfigError: ключа нет вне local/ci.
    """
    key = settings.email_code.hmac_key
    raw = key.get_secret_value().encode() if key is not None else b""
    if raw:
        return raw
    if settings.environment in (Environment.LOCAL, Environment.CI):
        return stdlib_secrets.token_bytes(MIN_KEY_BYTES * 2)
    raise KeyConfigError("GV_EMAIL_CODE__HMAC_KEY обязателен вне local/ci")


def limit_config(settings: Settings) -> LimitConfig:
    """Лимиты частоты из настроек."""
    login, mail = settings.login_limit, settings.email_limit
    return LimitConfig(
        login_per_ip=login.per_ip,
        login_ip_window_s=login.ip_window_s,
        login_per_account_subnet=login.per_account_subnet,
        login_per_account=login.per_account,
        login_account_window_s=login.account_window_s,
        subnet_v4_bits=login.subnet_v4_bits,
        subnet_v6_bits=login.subnet_v6_bits,
        register_per_ip_per_min=login.register_per_ip_per_min,
        reset_per_ip_per_min=login.reset_per_ip_per_min,
        email_per_address=mail.per_address,
        email_address_window_s=mail.window_s,
    )


def guest_config(settings: Settings) -> GuestConfig:
    """Настройки гостевых сессий → параметры use-cases."""
    g = settings.guest
    return GuestConfig(
        per_ip_per_hour=g.per_ip_per_hour,
        refresh_ttl_s=g.refresh_ttl_s,
        revoked_retention_s=g.revoked_retention_s,
        purge_batch=g.purge_batch,
        purge_max_batches=g.purge_max_batches,
    )


def signing_keys(settings: Settings, *, serving: bool) -> list[SigningKey]:
    """Ключи из ``GV_JWT__SIGNING_KEYS``; без них — временный ключ только в local/ci.

    ``serving=False`` — сборка приложения без инфраструктуры (экспорт OpenAPI).
    """
    raw = settings.jwt.signing_keys
    if raw is not None and raw.get_secret_value().strip():
        return parse_signing_keys(raw.get_secret_value())
    if serving and settings.environment not in (Environment.LOCAL, Environment.CI):
        raise KeyConfigError("GV_JWT__SIGNING_KEYS обязателен вне local/ci")
    if serving:
        log.warning("auth.ephemeral_signing_key")
    return [generate_dev_key()]


def realtime_signer(settings: Settings, *, serving: bool) -> HmacRealtimeTokenSigner | None:
    """Подписант connection-токенов Centrifugo из ``GV_REALTIME__CONNECTION_TOKEN_SECRET``.

    Без секрета вне local/ci процесс не стартует (fail-closed, как ``GV_JWT__SIGNING_KEYS``):
    ошибка конфигурации видна при деплое, а не первым игроком онлайна. В local/ci (и при
    сборке без инфраструктуры — экспорт OpenAPI) — ``None``: ручка отвечает 503
    ``realtime.not_configured``, остальной identity работает без Centrifugo.

    Секрет subscription-токенов identity иметь не должен (D9: им владеет только match) —
    его наличие тоже ошибка конфигурации.

    Raises:
        KeyConfigError: нет секрета вне local/ci, задан чужой секрет или ``aud`` совпал
            с ``aud`` access- или сервисных токенов.
    """
    rt = settings.realtime
    if rt is not None and rt.subscription_token_secret is not None:
        raise KeyConfigError(
            "GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET не должен быть у identity (D9)"
        )
    # Защита в глубину к разным алгоритмам: и aud не должен совпасть с access/internal.
    if rt is not None and rt.token_audience in (settings.jwt_audience, settings.internal_audience):
        raise KeyConfigError("GV_REALTIME__TOKEN_AUDIENCE совпадает с aud токенов сервисов")
    secret = rt.connection_token_secret if rt is not None else None
    if secret is not None:
        return HmacRealtimeTokenSigner(secret.get_secret_value())
    if serving and settings.environment not in (Environment.LOCAL, Environment.CI):
        raise KeyConfigError("GV_REALTIME__CONNECTION_TOKEN_SECRET обязателен вне local/ci")
    if serving:
        log.warning("realtime.not_configured")
    return None


def realtime_config(settings: Settings) -> RealtimeTokenConfig:
    """Настройки → параметры выдачи connection-токенов."""
    limit = settings.realtime_token_limit
    defaults = RealtimeTokenConfig()
    rt = settings.realtime
    return RealtimeTokenConfig(
        ttl_s=rt.token_ttl_s if rt is not None else defaults.ttl_s,
        audience=rt.token_audience if rt is not None else defaults.audience,
        per_user=limit.per_user,
        window_s=limit.window_s,
    )


@dataclass(frozen=True, slots=True)
class BootstrapAdminCredentials:
    """Учётные данные bootstrap-админа (разобранные настройки)."""

    login: str
    password: str
    nickname: str | None


def bootstrap_admin_credentials(settings: Settings) -> BootstrapAdminCredentials | None:
    """Учётные данные bootstrap-админа, если функция включена, иначе ``None``.

    Функция допустима только в local/ci. Заданные ``GV_BOOTSTRAP_ADMIN__*`` вне этих
    окружений — ошибка конфигурации: админ с известным паролем не должен появляться в
    staging/production (ср. ``GV_JWT__SIGNING_KEYS`` в ``signing_keys``).

    Raises:
        KeyConfigError: учётные данные заданы в staging/production.
    """
    b = settings.bootstrap_admin
    # Хоть что-то задано — функция «тронута»: проверяем окружение даже при половинной
    # настройке, чтобы одинокий login или password в проде не прошёл молча (fail-closed).
    if not (b.login or b.password.get_secret_value()):
        return None
    if settings.environment not in (Environment.LOCAL, Environment.CI):
        raise KeyConfigError("GV_BOOTSTRAP_ADMIN__* допустим только в local/ci")
    if not b.enabled:
        raise KeyConfigError("GV_BOOTSTRAP_ADMIN__*: нужны оба поля — login и password")
    return BootstrapAdminCredentials(
        login=b.login, password=b.password.get_secret_value(), nickname=b.nickname or None
    )


MIN_PROD_CODE_LENGTH = 6


def check_email_codes_config(settings: Settings) -> None:
    """Код из письма — секрет: ``backend=log`` и временный ключ HMAC допустимы только в local/ci.

    Проверка на старте процесса, а не в валидаторе ``Settings``: экспорт контрактов строит
    настройки в окружении по умолчанию (production) и не должен падать (ADR 0041).

    Raises:
        KeyConfigError: ``backend=log`` или пустой ``GV_EMAIL_CODE__HMAC_KEY`` в staging/production.
    """
    if settings.environment in (Environment.LOCAL, Environment.CI):
        return
    if settings.email.backend == "log":
        raise KeyConfigError("GV_EMAIL__BACKEND=log допустим только в local/ci")
    if settings.email_code.hmac_key is None or not settings.email_code.hmac_key.get_secret_value():
        raise KeyConfigError("GV_EMAIL_CODE__HMAC_KEY обязателен вне local/ci")
    if settings.email_code.length < MIN_PROD_CODE_LENGTH:
        raise KeyConfigError(
            f"GV_EMAIL_CODE__LENGTH вне local/ci не меньше {MIN_PROD_CODE_LENGTH} цифр"
        )


async def ensure_bootstrap_admin(settings: Settings) -> None:
    """Идемпотентно создать bootstrap-админа (команда ``migrate``, после миграций).

    Строит собственную короткоживущую инфраструктуру и закрывает её: job ``migrate``
    выполняется один раз до старта процессов сервиса. Вне local/ci с заданными учётными
    данными — ``KeyConfigError`` (см. ``bootstrap_admin_credentials``); выключено — no-op.
    """
    creds = bootstrap_admin_credentials(settings)
    if creds is None:
        return
    infra = await Infrastructure.open(settings, TABLES)
    try:
        sessions = infra.sessions
        if sessions is None:
            raise DependencyUnavailableError("bootstrap-админ требует GV_DATABASE_URL")
        clock = infra.clock
        hasher = Argon2Hasher(
            time_cost=settings.argon2.time_cost,
            memory_kib=settings.argon2.memory_kib,
            parallelism=settings.argon2.parallelism,
            max_concurrency=settings.argon2.max_concurrency,
        )

        def uow() -> IdentityUnitOfWork:
            return SqlIdentityUnitOfWork(sessions, producer=settings.service_name, clock=clock)

        ensure = EnsureBootstrapAdmin(
            uow,
            hasher=hasher,
            clock=clock,
            password_policy=PasswordPolicy(banned=common_passwords()),
        )
        await ensure(login=creds.login, password=creds.password, nickname=creds.nickname)
    finally:
        await infra.close()


async def reannounce_staff_perms(settings: Settings) -> int:
    """Повторно опубликовать права сотрудников (команда ``migrate``, по флагу настроек)."""
    infra = await Infrastructure.open(settings, TABLES)
    try:
        sessions = infra.sessions
        if sessions is None:
            raise DependencyUnavailableError("бэкфилл прав требует GV_DATABASE_URL")
        clock = infra.clock

        def uow() -> IdentityUnitOfWork:
            return SqlIdentityUnitOfWork(sessions, producer=settings.service_name, clock=clock)

        return await ReannounceStaffPerms(uow)()
    finally:
        await infra.close()


class _Unavailable:
    """Заглушка Redis-портов, когда Redis не сконфигурирован (экспорт OpenAPI)."""

    async def _fail(self, *_args: object, **_kwargs: object) -> Any:
        raise DependencyUnavailableError("redis не настроен")

    hit = forgive = reset = revoke = put = pop = spend = _fail


def build_api_deps(infra: Infrastructure) -> ApiDeps:
    """Собрать use-cases и аутентификацию для API."""
    settings = infra.settings
    if not isinstance(settings, Settings):
        raise TypeError("identity ожидает identity_service.settings.Settings")
    serving = infra.sessions is not None
    # Защита в глубину: заданные bootstrap-creds вне local/ci валят старт любого процесса
    # (сам bootstrap выполняется в migrate — см. ensure_bootstrap_admin).
    bootstrap_admin_credentials(settings)
    if serving:  # экспорт контрактов собирает deps без БД и в окружении по умолчанию
        check_email_codes_config(settings)
    clock, config = infra.clock, auth_config(settings)
    keys = StaticKeyProvider(signing_keys(settings, serving=serving), clock)

    def uow() -> IdentityUnitOfWork:
        if infra.sessions is None:
            raise DependencyUnavailableError("база данных не настроена")
        return SqlIdentityUnitOfWork(infra.sessions, producer=settings.service_name, clock=clock)

    prefix = settings.redis_key_prefix
    if infra.redis is not None:
        limiter: Any = RedisRateLimiter(infra.redis, prefix=prefix)
        strict_limiter: Any = RedisStrictRateLimiter(infra.redis, prefix=prefix)
        # Denylist должен пережить и access-токен, и допуск верификатора по exp.
        revocation: Any = RedisRevocationStore(
            infra.redis, ttl_s=settings.jwt.access_ttl_s + settings.jwt.leeway_s, prefix=prefix
        )
        states: Any = RedisOAuthStateStore(infra.redis, prefix=prefix)
    else:
        limiter = strict_limiter = revocation = states = _Unavailable()

    hasher = Argon2Hasher(
        time_cost=settings.argon2.time_cost,
        memory_kib=settings.argon2.memory_kib,
        parallelism=settings.argon2.parallelism,
        max_concurrency=settings.argon2.max_concurrency,
    )
    secrets = SystemSecrets()
    tokens = JwtIssuer(
        keys,
        clock,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        access_ttl_s=settings.jwt.access_ttl_s,
    )
    issuer = SessionIssuer(tokens, secrets, clock, refresh_ttl_s=config.refresh_ttl_s)
    guests = guest_config(settings)
    guest_issuer = GuestSessionIssuer(tokens, issuer, clock, refresh_ttl_s=guests.refresh_ttl_s)
    authn = PasswordAuthenticator(hasher, limiter, clock, config)
    email = _email_sender(settings)
    providers = _providers(settings)
    # Списание попытки кода идёт в своей транзакции — ей нужна фабрика сессий, а не uow.
    attempts: Any = (
        SqlChallengeAttempts(infra.sessions) if infra.sessions is not None else _Unavailable()
    )
    codes = CodeChallenges(
        codes=HmacCodeService(code_hmac_key(settings) if serving else b"\0" * MIN_KEY_BYTES),
        attempts=attempts,
        limiter=limiter,
        secrets=secrets,
        clock=clock,
        config=config,
    )

    use_cases = IdentityUseCases(
        config=config,
        register=RegisterWithEmail(
            uow,
            hasher=hasher,
            limiter=limiter,
            email=email,
            codes=codes,
            clock=clock,
            config=config,
        ),
        verify_email=VerifyEmail(uow, clock, config, codes=codes, sessions=issuer),
        login=LoginWithPassword(
            uow, authn, issuer, codes=codes, limiter=limiter, email=email, config=config
        ),
        confirm_login=ConfirmLogin(uow, codes=codes, sessions=issuer),
        resend_code=ResendCode(uow, codes=codes, limiter=limiter, email=email, config=config),
        request_password_change=RequestPasswordChange(
            uow, authn, codes=codes, limiter=limiter, email=email, config=config
        ),
        confirm_password_change=ConfirmPasswordChange(
            uow, hasher, revocation, clock, config, email=email, codes=codes
        ),
        admin_login=AdminLogin(uow, authn, issuer),
        refresh=RefreshSession(uow, issuer, revocation, clock, guests=guest_issuer),
        logout=Logout(uow, revocation, clock),
        logout_all=LogoutAll(uow, revocation, clock),
        request_password_reset=RequestPasswordReset(
            uow, limiter=limiter, email=email, codes=codes, clock=clock, config=config
        ),
        reset_password=ResetPassword(
            uow, hasher, revocation, clock, config, email=email, codes=codes, sessions=issuer
        ),
        start_oauth=StartOAuth(uow, providers, states, secrets, config, authenticator=authn),
        complete_oauth=CompleteOAuth(
            uow,
            providers,
            states,
            LinkExternalAccount(clock),
            LoginWithLinkedAccount(issuer),
            email=email,
        ),
        unlink=UnlinkExternalAccount(uow, authn, email),
        grant_consents=GrantConsents(uow, clock, config),
        delete_account=DeleteAccount(uow, authn, revocation, clock),
        assign_roles=AssignRoles(uow, clock, revocation),
        update_role_matrix=UpdateRoleMatrix(uow, clock, revocation),
        get_user=GetUser(uow),
        list_users=ListUsers(uow),
        list_linked=ListLinkedAccounts(uow),
        get_role_matrix=GetRoleMatrix(uow),
        get_jwks=GetJwks(keys),
        issue_service_token=IssueServiceToken(
            service_clients(settings),
            tokens,
            limiter,
            ServiceTokenConfig(
                ttl_s=settings.service_token.ttl_s,
                failures_per_client_ip_per_min=(
                    settings.service_token_limit.failures_per_client_ip_per_min
                ),
                success_per_client_per_min=settings.service_token_limit.success_per_client_per_min,
            ),
        ),
        list_verified_emails=ListVerifiedEmails(
            uow,
            strict_limiter,
            UserEmailsLimit(
                per_client=settings.users_emails_limit.per_client,
                window_s=settings.users_emails_limit.window_s,
                ids_per_hour=settings.users_emails_limit.ids_per_hour,
            ),
        ),
        guest=GuestUseCases(
            config=guests,
            start=StartGuestSession(
                uow, guest_issuer, limiter, clock, guests, on_started=GUEST_SESSIONS_STARTED.inc
            ),
            end=EndGuestSession(uow, revocation, clock),
        ),
        issue_realtime_token=IssueRealtimeToken(
            realtime_signer(settings, serving=serving), limiter, clock, realtime_config(settings)
        ),
    )

    async def load_jwks() -> Mapping[str, Any]:
        return keys.jwks()

    verifier = JwtVerifier(
        load_jwks,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        # Свои ключи меняются только рестартом — кэш можно обновлять часто.
        cache_ttl_s=60,
        leeway_s=settings.jwt.leeway_s,
        revocation=(
            RedisRevocationChecker(infra.redis, prefix=prefix) if infra.redis is not None else None
        ),
        on_revocation_unavailable=REVOCATION_FAIL_OPEN.inc,
    )
    # Сервисные токены internal-ручек: те же ключи, но свой aud (D13). Отзыва по sid нет —
    # у сервисного токена нет сессии, его срок ограничен TTL.
    service_verifier = JwtVerifier(
        load_jwks,
        issuer=settings.jwt_issuer,
        audience=settings.internal_audience,
        cache_ttl_s=60,
        leeway_s=settings.jwt.leeway_s,
    )
    return ApiDeps(
        use_cases=use_cases,
        auth=Authenticator(verifier),
        settings=settings,
        service_auth=ServiceAuthenticator(service_verifier),
        verifier=verifier,
    )


def service_clients(settings: Settings) -> ServiceClientRegistry:
    """Клиенты сервисных токенов из ``GV_SERVICE_CLIENTS``.

    Raises:
        InvalidInputError: некорректная конфигурация (процесс не стартует).
    """
    return ServiceClientRegistry(
        ServiceClient.create(
            c.client_id,
            c.secret_sha256,
            c.audiences,
            forbidden_audiences=(settings.jwt_audience,),
        )
        for c in settings.service_clients
    )


def build_periodic_jobs(infra: Infrastructure) -> list[PeriodicJob]:
    """Периодические задачи identity (кроме встроенных очисток ``gv_service``)."""
    settings = infra.settings
    if not isinstance(settings, Settings) or infra.sessions is None:
        return []
    guest = settings.guest
    purge = PurgeGuestSessions(
        SqlGuestSessionPurger(infra.sessions), infra.clock, guest_config(settings)
    )
    codes = settings.email_code
    purge_codes = PurgeEmailChallenges(
        SqlChallengePurger(infra.sessions), infra.clock, code_config(settings)
    )
    return [
        PeriodicJob("guest_sessions.purge", guest.purge_interval_s, purge, guest.purge_jitter_s),
        PeriodicJob(
            "email_challenges.purge", codes.purge_interval_s, purge_codes, codes.purge_jitter_s
        ),
    ]


def _email_sender(settings: Settings) -> EmailSender:
    ttl_s = settings.email_code.ttl_s
    if settings.email.backend == "smtp":
        smtp = settings.smtp
        # SMTP - в фоне: время ответа не должно зависеть от почтового сервера.
        return IdentityEmailSender(
            BackgroundEmailSender(
                SmtpEmailSender(
                    host=smtp.host,
                    port=smtp.port,
                    sender=settings.email.sender,
                    username=smtp.username,
                    password=smtp.password.get_secret_value() if smtp.password else None,
                    start_tls=smtp.start_tls,
                    use_tls=smtp.use_tls,
                    timeout_s=smtp.timeout_s,
                )
            ),
            code_ttl_s=ttl_s,
        )
    # Код из письма - секрет: в лог его пишем только в local/ci (e2e читают его оттуда).
    return IdentityEmailSender(
        LogEmailSender(include_secrets=settings.environment in (Environment.LOCAL, Environment.CI)),
        code_ttl_s=ttl_s,
    )


def _providers(settings: Settings) -> dict[Provider, OAuthProvider]:
    y = settings.yandex
    if not y.enabled:
        return {}
    if not (y.client_id and y.client_secret.get_secret_value() and y.redirect_uri):
        raise KeyConfigError(
            "GV_YANDEX__ENABLED=true требует CLIENT_ID, CLIENT_SECRET, REDIRECT_URI"
        )
    return {
        Provider.YANDEX: YandexOAuthProvider(
            client_id=y.client_id,
            client_secret=y.client_secret.get_secret_value(),
            redirect_uri=y.redirect_uri,
            authorize_url=y.authorize_url,
            token_url=y.token_url,
            info_url=y.info_url,
        )
    }
