"""Привязка внешних аккаунтов (Яндекс ID) и вход через уже привязанный аккаунт (D2).

Поток authorization code + PKCE S256 (§8 п.5):

1. ``StartOAuth`` создаёт одноразовый ``state`` (в хранилище — по хэшу), PKCE verifier и
   nonce привязки к браузеру (cookie). Для привязки ``state`` связан с ``sid`` вошедшего.
2. ``CompleteOAuth`` атомарно забирает ``state`` (повтор невозможен), сверяет nonce из
   cookie, обменивает код и получает идентификатор у провайдера.
3. Привязка — только к пользователю из ``state`` и только при живой сессии; вход — только
   если аккаунт провайдера уже привязан. Совпадение e-mail ничего не связывает.
"""

from __future__ import annotations

import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.errors import (
    ConflictError,
    FeatureDisabledError,
    NotFoundError,
    UnauthenticatedError,
)
from identity_service.application.config import AuthConfig
from identity_service.application.dto import Actor
from identity_service.application.ports import (
    ExternalProfile,
    IdentityUnitOfWork,
    OAuthProvider,
    OAuthState,
    OAuthStateStore,
    SecretGenerator,
    UnitOfWorkFactory,
)
from identity_service.application.security import (
    OAuthStateError,
    hash_secret,
    pkce_challenge,
    safe_return_to,
)
from identity_service.application.sessions import IssuedSession, SessionIssuer
from identity_service.domain import AuthIdentity, Provider, SessionKind

log = structlog.get_logger(__name__)

PURPOSE_LINK = "link"
PURPOSE_LOGIN = "login"
STATE_BYTES = 32
VERIFIER_BYTES = 48  # 64 символа base64url: в пределах 43..128 (RFC 7636 §4.1)


def _state_key(state: str) -> str:
    return hash_secret(state)


@dataclass(frozen=True, slots=True)
class StartedOAuth:
    """URL авторизации и nonce, который API кладёт в cookie браузера."""

    authorize_url: str
    browser_nonce: str


@dataclass(frozen=True, slots=True)
class CompletedOAuth:
    """Итог callback: куда вернуть пользователя и, для входа, выданная сессия."""

    purpose: str
    provider: Provider
    return_to: str
    session: IssuedSession | None = None


class _Providers:
    def __init__(self, providers: Mapping[Provider, OAuthProvider]) -> None:
        self._providers = providers

    def get(self, provider: Provider) -> OAuthProvider:
        found = self._providers.get(provider)
        if found is None:
            raise FeatureDisabledError(
                "вход через этот сервис выключен", code="auth.provider_disabled"
            )
        return found


class StartOAuth:
    """Начать привязку (нужен вход) или вход через привязанный аккаунт."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        providers: Mapping[Provider, OAuthProvider],
        states: OAuthStateStore,
        secrets: SecretGenerator,
        config: AuthConfig,
    ) -> None:
        self._uow, self._providers, self._states = uow, _Providers(providers), states
        self._secrets, self._config = secrets, config

    async def __call__(
        self,
        provider: Provider,
        *,
        purpose: str,
        actor: Actor | None,
        return_to: str | None,
    ) -> StartedOAuth:
        """Подготовить редирект к провайдеру.

        Raises:
            UnauthenticatedError: привязка без входа.
            ConflictError: ``auth.provider_already_linked`` — у пользователя уже есть привязка.
            FeatureDisabledError: провайдер выключен.
        """
        oauth = self._providers.get(provider)
        target = safe_return_to(return_to, self._config.return_to_prefixes)
        if purpose == PURPOSE_LINK:
            if actor is None or actor.session_id is None:
                raise UnauthenticatedError("для привязки нужно войти")
            async with self._uow() as uow:
                if await uow.identities.get_for_user(actor.user_id, provider) is not None:
                    raise ConflictError("сервис уже привязан", code="auth.provider_already_linked")
        elif purpose != PURPOSE_LOGIN:
            raise ValueError(f"неизвестная цель OAuth: {purpose}")

        state = self._secrets.token(STATE_BYTES)
        verifier = self._secrets.token(VERIFIER_BYTES)
        nonce = self._secrets.token(STATE_BYTES)
        await self._states.put(
            _state_key(state),
            OAuthState(
                purpose=purpose,
                provider=provider,
                user_id=actor.user_id if purpose == PURPOSE_LINK and actor else None,
                session_id=actor.session_id if purpose == PURPOSE_LINK and actor else None,
                code_verifier=verifier,
                browser_binding=hash_secret(nonce),
                return_to=target,
            ),
            self._config.oauth_state_ttl_s,
        )
        url = oauth.authorize_url(state=state, code_challenge=pkce_challenge(verifier))
        return StartedOAuth(authorize_url=url, browser_nonce=nonce)


class LinkExternalAccount:
    """Привязать аккаунт провайдера к пользователю (в транзакции вызывающего)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    async def __call__(
        self, uow: IdentityUnitOfWork, user_id: UUID, provider: Provider, profile: ExternalProfile
    ) -> None:
        """Привязать.

        Raises:
            ConflictError: ``auth.provider_account_taken`` — аккаунт провайдера привязан к
                другому пользователю; ``auth.provider_already_linked`` — у пользователя уже
                привязан другой аккаунт этого провайдера.
        """
        existing = await uow.identities.get(provider, profile.subject)
        if existing is not None:
            if existing.user_id != user_id:
                raise ConflictError(
                    "этот аккаунт уже привязан к другому пользователю",
                    code="auth.provider_account_taken",
                )
            return  # повторная привязка того же аккаунта — идемпотентно
        if await uow.identities.get_for_user(user_id, provider) is not None:
            raise ConflictError("сервис уже привязан", code="auth.provider_already_linked")
        user = await uow.users.get(user_id)
        if user is None or not user.is_active:
            raise UnauthenticatedError("аккаунт недоступен")
        await uow.identities.add(
            AuthIdentity(
                user_id=user_id,
                provider=provider,
                subject=profile.subject,
                display=profile.display,
                linked_at=self._clock.now(),
            )
        )
        user.link_provider(provider)
        await uow.users.save(user)
        uow.track(user)


class LoginWithLinkedAccount:
    """Вход через уже привязанный аккаунт провайдера (в транзакции вызывающего)."""

    def __init__(self, sessions: SessionIssuer) -> None:
        self._sessions = sessions

    async def __call__(
        self,
        uow: IdentityUnitOfWork,
        provider: Provider,
        profile: ExternalProfile,
        *,
        ip: str | None,
    ) -> IssuedSession:
        """Открыть web-сессию.

        Raises:
            ConflictError: ``auth.provider_not_linked`` — аккаунт провайдера ни к кому не
                привязан (новый аккаунт не создаётся: D2).
        """
        identity = await uow.identities.get(provider, profile.subject)
        if identity is None:
            raise ConflictError(
                "аккаунт не привязан: войдите паролем и привяжите его в профиле",
                code="auth.provider_not_linked",
            )
        user = await uow.users.get(identity.user_id)
        if user is None or not user.is_active:
            raise UnauthenticatedError("аккаунт недоступен")
        matrix = await uow.roles.get_matrix()
        return await self._sessions.open(uow, user, matrix, kind=SessionKind.WEB, ip=ip)


class CompleteOAuth:
    """Общий callback провайдера: привязка или вход в зависимости от ``state``."""

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        providers: Mapping[Provider, OAuthProvider],
        states: OAuthStateStore,
        link: LinkExternalAccount,
        login: LoginWithLinkedAccount,
    ) -> None:
        self._uow, self._providers, self._states = uow, _Providers(providers), states
        self._link, self._login = link, login

    async def __call__(
        self,
        provider: Provider,
        *,
        state: str,
        code: str,
        browser_nonce: str | None,
        actor: Actor | None,
        ip: str | None,
    ) -> CompletedOAuth:
        """Завершить OAuth.

        Raises:
            OAuthStateError: ``state`` неизвестен, использован, истёк, от другого провайдера
                или начат в другом браузере.
        """
        oauth = self._providers.get(provider)
        saved = await self._states.pop(_state_key(state)) if state else None
        if saved is None or saved.provider is not provider:
            raise OAuthStateError("недействительный state")
        if not browser_nonce or not hmac.compare_digest(
            saved.browser_binding, hash_secret(browser_nonce)
        ):
            raise OAuthStateError("вход начат в другом браузере")
        if saved.purpose == PURPOSE_LINK and actor is not None and actor.user_id != saved.user_id:
            raise OAuthStateError("привязку начал другой пользователь")

        access_token = await oauth.exchange_code(code=code, code_verifier=saved.code_verifier)
        profile = await oauth.fetch_profile(access_token)

        async with self._uow() as uow:
            if saved.purpose == PURPOSE_LINK:
                await self._ensure_link_session(uow, saved)
                assert saved.user_id is not None  # noqa: S101 — гарантирует _ensure_link_session
                await self._link(uow, saved.user_id, provider, profile)
                await uow.commit()
                log.info("auth.provider_linked", user_id=str(saved.user_id), provider=provider)
                return CompletedOAuth(PURPOSE_LINK, provider, saved.return_to)
            issued = await self._login(uow, provider, profile, ip=ip)
            await uow.commit()
        log.info("auth.login", user_id=str(issued.user_id), method=provider.value)
        return CompletedOAuth(PURPOSE_LOGIN, provider, saved.return_to, issued)

    @staticmethod
    async def _ensure_link_session(uow: IdentityUnitOfWork, saved: OAuthState) -> None:
        if saved.session_id is None or saved.user_id is None:
            raise OAuthStateError("недействительный state")
        session = await uow.sessions.get(saved.session_id)
        if session is None or not session.is_active or session.user_id != saved.user_id:
            raise UnauthenticatedError("сессия завершена", code="auth.session_revoked")


class UnlinkExternalAccount:
    """Отвязать внешний сервис."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def __call__(self, user_id: UUID, provider: Provider) -> None:
        """Отвязать.

        Raises:
            NotFoundError: ``auth.provider_not_linked``.
        """
        async with self._uow() as uow:
            user = await uow.users.get(user_id)
            if user is None or not await uow.identities.remove(user_id, provider):
                raise NotFoundError("сервис не привязан", code="auth.provider_not_linked")
            user.unlink_provider(provider)
            await uow.users.save(user)
            uow.track(user)
            await uow.commit()
