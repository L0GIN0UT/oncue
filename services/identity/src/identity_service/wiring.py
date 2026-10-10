"""Composition root identity: адаптеры → use-cases → API (DI без фреймворка, ADR 0011).

Модуль вне слоёв (как ``main``): только он знает и инфраструктуру, и API.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any

import structlog
from prometheus_client import Counter

from gv_auth import Authenticator, JwtVerifier, RedisRevocationChecker
from gv_core.errors import DependencyUnavailableError
from gv_service import Environment, Infrastructure, PeriodicJob
from identity_service.api.common import ApiDeps
from identity_service.application.commands.account import DeleteAccount, GrantConsents
from identity_service.application.commands.auth import (
    AdminLogin,
    LoginWithPassword,
    Logout,
    LogoutAll,
    PasswordAuthenticator,
    RefreshSession,
    RegisterWithEmail,
    RequestPasswordReset,
    ResetPassword,
    VerifyEmail,
)
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
from identity_service.application.commands.roles import AssignRoles, UpdateRoleMatrix
from identity_service.application.config import AuthConfig, LimitConfig
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
from identity_service.application.sessions import SessionIssuer
from identity_service.domain import (
    EmailAddress,
    PasswordPolicy,
    Provider,
    parse_consent_requirements,
)
from identity_service.infrastructure.clients import (
    LogEmailSender,
    SmtpEmailSender,
    YandexOAuthProvider,
)
from identity_service.infrastructure.db.guest_sessions import SqlGuestSessionPurger
from identity_service.infrastructure.db.repositories import SqlIdentityUnitOfWork
from identity_service.infrastructure.redis_stores import (
    RedisOAuthStateStore,
    RedisRateLimiter,
    RedisRevocationStore,
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
        verify_link=settings.email.verify_link,
        reset_link=settings.email.reset_link,
        return_to_prefixes=settings.oauth.return_to_prefixes,
        password_policy=PasswordPolicy(banned=common_passwords()),
        limits=limit_config(settings),
    )


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


class _Unavailable:
    """Заглушка Redis-портов, когда Redis не сконфигурирован (экспорт OpenAPI)."""

    async def _fail(self, *_args: object, **_kwargs: object) -> Any:
        raise DependencyUnavailableError("redis не настроен")

    hit = forgive = reset = revoke = put = pop = _fail


def build_api_deps(infra: Infrastructure) -> ApiDeps:
    """Собрать use-cases и аутентификацию для API."""
    settings = infra.settings
    if not isinstance(settings, Settings):
        raise TypeError("identity ожидает identity_service.settings.Settings")
    serving = infra.sessions is not None
    clock, config = infra.clock, auth_config(settings)
    keys = StaticKeyProvider(signing_keys(settings, serving=serving), clock)

    def uow() -> IdentityUnitOfWork:
        if infra.sessions is None:
            raise DependencyUnavailableError("база данных не настроена")
        return SqlIdentityUnitOfWork(infra.sessions, producer=settings.service_name, clock=clock)

    prefix = settings.redis_key_prefix
    if infra.redis is not None:
        limiter: Any = RedisRateLimiter(infra.redis, prefix=prefix)
        # Denylist должен пережить и access-токен, и допуск верификатора по exp.
        revocation: Any = RedisRevocationStore(
            infra.redis, ttl_s=settings.jwt.access_ttl_s + settings.jwt.leeway_s, prefix=prefix
        )
        states: Any = RedisOAuthStateStore(infra.redis, prefix=prefix)
    else:
        limiter = revocation = states = _Unavailable()

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

    use_cases = IdentityUseCases(
        config=config,
        register=RegisterWithEmail(
            uow,
            hasher=hasher,
            limiter=limiter,
            email=email,
            secrets=secrets,
            clock=clock,
            config=config,
        ),
        verify_email=VerifyEmail(uow, clock, config),
        login=LoginWithPassword(uow, authn, issuer),
        admin_login=AdminLogin(uow, authn, issuer),
        refresh=RefreshSession(uow, issuer, revocation, clock, guests=guest_issuer),
        logout=Logout(uow, revocation, clock),
        logout_all=LogoutAll(uow, revocation, clock),
        request_password_reset=RequestPasswordReset(
            uow, limiter=limiter, email=email, secrets=secrets, clock=clock, config=config
        ),
        reset_password=ResetPassword(uow, hasher, revocation, clock, config, email=email),
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
        guest=GuestUseCases(
            config=guests,
            start=StartGuestSession(
                uow, guest_issuer, limiter, clock, guests, on_started=GUEST_SESSIONS_STARTED.inc
            ),
            end=EndGuestSession(uow, revocation, clock),
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
    return ApiDeps(
        use_cases=use_cases, auth=Authenticator(verifier), settings=settings, verifier=verifier
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
    return [
        PeriodicJob("guest_sessions.purge", guest.purge_interval_s, purge, guest.purge_jitter_s)
    ]


def _email_sender(settings: Settings) -> EmailSender:
    if settings.email.backend == "smtp":
        smtp = settings.smtp
        return SmtpEmailSender(
            host=smtp.host,
            port=smtp.port,
            sender=settings.email.sender,
            username=smtp.username,
            password=smtp.password.get_secret_value() if smtp.password else None,
            start_tls=smtp.start_tls,
        )
    # Ссылки с секретом пишем в лог только вне production (демонстрация этапа 1).
    return LogEmailSender(include_links=not settings.is_production)


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
