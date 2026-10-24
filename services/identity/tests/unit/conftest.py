"""Фейки портов identity для unit-тестов use-cases (без IO).

Общие фейки (часы, Unit of Work) — из gv_testkit; здесь только порты самого identity.
Транзакции эмулируются снимком: UoW работает с копией данных и публикует её при commit,
поэтому откат (исключение до commit) честно ничего не сохраняет.
"""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Self
from uuid import UUID

import pytest
from identity_service.application.commands.account import DeleteAccount, GrantConsents
from identity_service.application.commands.auth import (
    AdminLogin,
    ConfirmLogin,
    ConfirmPasswordChange,
    LoginResult,
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
from identity_service.application.commands.codes import CodeChallenges
from identity_service.application.commands.oauth import (
    CompleteOAuth,
    LinkExternalAccount,
    LoginWithLinkedAccount,
    StartOAuth,
    UnlinkExternalAccount,
)
from identity_service.application.commands.roles import AssignRoles, UpdateRoleMatrix
from identity_service.application.commands.service_tokens import (
    IssueServiceToken,
    ServiceTokenConfig,
)
from identity_service.application.config import AuthConfig, LimitConfig
from identity_service.application.container import IdentityUseCases
from identity_service.application.ports import (
    ChallengePurpose,
    EmailMessage,
    ExternalProfile,
    IssuedAccess,
    OAuthState,
    RateDecision,
    RoleChange,
)
from identity_service.application.ports.service_tokens import IssuedServiceToken
from identity_service.application.queries import (
    GetJwks,
    GetRoleMatrix,
    GetUser,
    ListLinkedAccounts,
    ListUsers,
)
from identity_service.application.queries.user_emails import ListVerifiedEmails, UserEmailsLimit
from identity_service.application.security import hash_secret
from identity_service.application.sessions import IssuedSession, SessionIssuer
from identity_service.domain import (
    AuthIdentity,
    Consent,
    ConsentRequirement,
    EmailAddress,
    EmailChallenge,
    Nickname,
    PasswordPolicy,
    Provider,
    RefreshToken,
    RoleMatrix,
    Session,
    TrustedDevice,
    User,
)
from identity_service.domain.service_client import ServiceClient, ServiceClientRegistry

from gv_core import ConflictError, DependencyUnavailableError, DomainEvent
from gv_core.ids import uuid7
from gv_testkit import FakeClock, FakeUnitOfWork, InMemoryIdempotencyStore

DEVICE = "device-token-known"  # cookie доверенного устройства make_user
ALL_PERMS = frozenset(
    {
        "admin.access",
        "users.view",
        "roles.assign",
        "roles.manage",
        "catalog.import",
        "catalog.block_item",
        "catalog.edit_aliases",
        "media.upload_source",
        "media.regenerate",
    }
)
SEED_MATRIX = RoleMatrix(
    roles={
        "user": frozenset(),
        "moderator": frozenset(
            {"admin.access", "users.view", "catalog.block_item", "catalog.edit_aliases"}
        ),
        "admin": ALL_PERMS,
    },
    permissions=ALL_PERMS,
)
CONSENTS = (
    ConsentRequirement("terms", "2026-10"),
    ConsentRequirement("privacy", "2026-10"),
)


@dataclass
class Data:
    users: dict[UUID, User] = field(default_factory=dict)
    creds: dict[UUID, str] = field(default_factory=dict)
    sessions: dict[UUID, Session] = field(default_factory=dict)
    refresh: dict[str, RefreshToken] = field(default_factory=dict)
    identities: dict[tuple[Provider, str], AuthIdentity] = field(default_factory=dict)
    matrix: RoleMatrix = SEED_MATRIX
    audit: list[RoleChange] = field(default_factory=list)
    consents: list[Consent] = field(default_factory=list)
    challenges: dict[UUID, EmailChallenge] = field(default_factory=dict)
    devices: dict[tuple[UUID, str], TrustedDevice] = field(default_factory=dict)


class Users:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def add(self, user: User) -> None:
        for u in self.d.users.values():
            if u.nickname.value.lower() == user.nickname.value.lower():
                raise ConflictError("nick", code="user.nickname_taken")
            if u.email.value == user.email.value:
                raise ConflictError("email", code="user.email_taken")
        user.persisted_version = user.version
        self.d.users[user.id] = user

    async def get(self, user_id: UUID) -> User | None:
        return self.d.users.get(user_id)

    async def find_by_email(self, email: EmailAddress) -> User | None:
        return next((u for u in self.d.users.values() if u.email.value == email.value), None)

    async def find_by_nickname(self, nickname: Nickname) -> User | None:
        low = nickname.value.lower()
        return next((u for u in self.d.users.values() if u.nickname.value.lower() == low), None)

    async def save(self, user: User) -> None:
        user.persisted_version = user.version
        self.d.users[user.id] = user

    async def search(self, query: str | None, *, after: UUID | None, limit: int) -> list[User]:
        users = sorted(self.d.users.values(), key=lambda u: u.id)
        if after:
            users = [u for u in users if u.id > after]
        if query:
            users = [u for u in users if u.nickname.value.lower().startswith(query.lower())]
        return users[:limit]

    async def ids_with_roles(self, roles: Iterable[str]) -> list[UUID]:
        wanted = set(roles)
        return [u.id for u in self.d.users.values() if u.is_active and u.roles & wanted]

    async def verified_emails(self, user_ids: Collection[UUID]) -> list[tuple[UUID, str]]:
        return [
            (u.id, u.email.value)
            for uid in user_ids
            if (u := self.d.users.get(uid)) is not None and u.is_active and u.email_verified
        ]


class Creds:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def get_password_hash(self, user_id: UUID) -> str | None:
        return self.d.creds.get(user_id)

    async def set_password_hash(self, user_id: UUID, password_hash: str, now: datetime) -> None:
        self.d.creds[user_id] = password_hash

    async def delete(self, user_id: UUID) -> None:
        self.d.creds.pop(user_id, None)


class Sessions:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def add(self, session: Session) -> None:
        self.d.sessions[session.id] = session

    async def get(self, session_id: UUID) -> Session | None:
        return self.d.sessions.get(session_id)

    async def save(self, session: Session) -> None:
        self.d.sessions[session.id] = session

    async def add_refresh(self, token: RefreshToken) -> None:
        self.d.refresh[token.token_hash] = token

    async def find_refresh_for_update(self, token_hash: str) -> RefreshToken | None:
        return self.d.refresh.get(token_hash)

    async def save_refresh(self, token: RefreshToken) -> None:
        self.d.refresh[token.token_hash] = token

    async def active_for_user(self, user_id: UUID) -> list[Session]:
        return [s for s in self.d.sessions.values() if s.user_id == user_id and s.is_active]


class Identities:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def get(self, provider: Provider, subject: str) -> AuthIdentity | None:
        return self.d.identities.get((provider, subject))

    async def get_for_user(self, user_id: UUID, provider: Provider) -> AuthIdentity | None:
        return next(
            (
                i
                for i in self.d.identities.values()
                if i.user_id == user_id and i.provider == provider
            ),
            None,
        )

    async def list_for_user(self, user_id: UUID) -> list[AuthIdentity]:
        return [i for i in self.d.identities.values() if i.user_id == user_id]

    async def add(self, identity: AuthIdentity) -> None:
        key = (identity.provider, identity.subject)
        if key in self.d.identities:
            raise ConflictError("taken", code="auth.provider_account_taken")
        self.d.identities[key] = identity

    async def remove(self, user_id: UUID, provider: Provider) -> bool:
        found = await self.get_for_user(user_id, provider)
        if found is None:
            return False
        del self.d.identities[(found.provider, found.subject)]
        return True


class Roles:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def get_matrix(self) -> RoleMatrix:
        return self.d.matrix

    async def save_matrix(self, matrix: RoleMatrix) -> None:
        self.d.matrix = matrix

    async def add_audit(self, change: RoleChange) -> None:
        self.d.audit.append(change)


class Consents:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def add(self, consents: Iterable[Consent]) -> None:
        for c in consents:
            if not any(
                (x.user_id, x.kind, x.version) == (c.user_id, c.kind, c.version)
                for x in self.d.consents
            ):
                self.d.consents.append(c)

    async def list_for_user(self, user_id: UUID) -> list[Consent]:
        return [c for c in self.d.consents if c.user_id == user_id]


class Challenges:
    """Коды из писем. Попытки, списанные отдельной транзакцией, лежат в ``world.spent``."""

    def __init__(self, d: Data, spent: dict[UUID, int], spent_total: dict[UUID, int]) -> None:
        # spent — попытки текущего кода (resend обнуляет), spent_total — за жизнь записи
        # (как колонка ``spent_total``: resend её не трогает, по ней считается суточный потолок).
        self.d, self.spent, self.spent_total = d, spent, spent_total

    def _merged(self, ch: EmailChallenge) -> EmailChallenge:
        return dataclasses.replace(ch, attempts=ch.attempts + self.spent.get(ch.id, 0))

    async def add(self, challenge: EmailChallenge) -> None:
        self.d.challenges[challenge.id] = challenge

    async def get(self, challenge_id: UUID) -> EmailChallenge | None:
        found = self.d.challenges.get(challenge_id)
        return self._merged(found) if found else None

    async def cancel_active(
        self, subject_hash: str, purpose: ChallengePurpose, now: datetime
    ) -> int:
        count = 0
        for key, ch in list(self.d.challenges.items()):
            if ch.subject_hash == subject_hash and ch.purpose is purpose and ch.is_active(now):
                self.d.challenges[key] = dataclasses.replace(ch, cancelled_at=now)
                count += 1
        return count

    async def replace_code(
        self, challenge_id: UUID, *, code_hash: str, expires_at: datetime, now: datetime
    ) -> bool:
        ch = self.d.challenges.get(challenge_id)
        if ch is None or not ch.is_active(now):
            return False
        self.spent.pop(challenge_id, None)
        self.d.challenges[challenge_id] = dataclasses.replace(
            ch,
            code_hash=code_hash,
            expires_at=expires_at,
            last_sent_at=now,
            send_count=ch.send_count + 1,
            attempts=0,
        )
        return True

    async def consume(self, challenge_id: UUID, now: datetime) -> bool:
        ch = self.d.challenges.get(challenge_id)
        if ch is None or not ch.is_active(now):
            return False
        self.d.challenges[challenge_id] = dataclasses.replace(ch, consumed_at=now)
        return True

    async def failures_since(self, subject_hash: str, since: datetime) -> int:
        total = 0
        for ch in self.d.challenges.values():
            if ch.subject_hash == subject_hash and ch.created_at >= since:
                total += self.spent_total.get(ch.id, 0) - (1 if ch.consumed_at else 0)
        return total

    async def delete_for_subjects(self, user_id: UUID, subject_hashes: Iterable[str]) -> int:
        hashes = set(subject_hashes)
        doomed = [
            key
            for key, ch in self.d.challenges.items()
            if ch.user_id == user_id or ch.subject_hash in hashes
        ]
        for key in doomed:
            del self.d.challenges[key]
        return len(doomed)


class Attempts:
    """Списание попытки вне транзакции use-case (как отдельная транзакция в БД)."""

    def __init__(self, world: World) -> None:
        self.w = world

    async def spend(self, challenge_id: UUID, now: datetime) -> EmailChallenge | None:
        ch = self.w.data.challenges.get(challenge_id)
        if ch is None or not ch.is_active(now):
            return None
        merged = dataclasses.replace(ch, attempts=ch.attempts + self.w.spent.get(ch.id, 0))
        if merged.is_exhausted:
            return None
        self.w.spent[ch.id] = self.w.spent.get(ch.id, 0) + 1
        self.w.spent_total[ch.id] = self.w.spent_total.get(ch.id, 0) + 1
        return dataclasses.replace(merged, attempts=merged.attempts + 1)


class Devices:
    def __init__(self, d: Data) -> None:
        self.d = d

    async def add(self, device: TrustedDevice) -> None:
        self.d.devices[(device.user_id, device.device_hash)] = device

    async def is_trusted(self, user_id: UUID, device_hash: str, now: datetime) -> bool:
        found = self.d.devices.get((user_id, device_hash))
        return found is not None and found.revoked_at is None and now < found.expires_at

    async def revoke_for_user(
        self, user_id: UUID, now: datetime, *, except_hash: str | None = None
    ) -> int:
        count = 0
        for key, dev in list(self.d.devices.items()):
            if key[0] == user_id and dev.device_hash != except_hash and dev.revoked_at is None:
                self.d.devices[key] = dataclasses.replace(dev, revoked_at=now)
                count += 1
        return count

    async def delete_for_user(self, user_id: UUID) -> int:
        doomed = [key for key in self.d.devices if key[0] == user_id]
        for key in doomed:
            del self.d.devices[key]
        return len(doomed)


class FakeCodeService:
    """Детерминированные коды: 000001, 000002, ... ; «HMAC» — строка с id."""

    def __init__(self) -> None:
        self.n = 0

    def generate(self, length: int) -> str:
        self.n += 1
        return f"{self.n:0{length}d}"

    def digest(self, *, challenge_id: UUID, code: str) -> str:
        return f"d|{challenge_id}|{code}"

    def matches(self, stored: str, *, challenge_id: UUID, code: str) -> bool:
        return stored == self.digest(challenge_id=challenge_id, code=code)


class World:
    """Все фейки и собранные на них use-cases."""

    def __init__(self, config: AuthConfig | None = None) -> None:
        self.data = Data()
        self.events: list[DomainEvent] = []
        self.clock = FakeClock()
        self.hasher = FakeHasher()
        self.limiter = FakeLimiter()
        self.strict_limiter = FakeStrictLimiter()
        self.revoked: set[UUID] = set()
        self.states = FakeStateStore()
        self.emails: list[EmailMessage] = []
        self.spent: dict[UUID, int] = {}
        self.spent_total: dict[UUID, int] = {}
        self.secrets = SeqSecrets()
        self.yandex = FakeOAuth()
        # Не транзакционный: откат мутации не моделирует (см. gv_testkit).
        self.idempotency = InMemoryIdempotencyStore(self.clock)
        self.config = config or AuthConfig(
            required_consents=CONSENTS,
            bootstrap_admin_emails=frozenset({"boss@example.com"}),
            return_to_prefixes=("/", "/profile"),
            password_policy=PasswordPolicy(banned=frozenset({"qwertyuiop"})),
        )
        self.tokens = FakeTokens()
        self.codes = FakeCodeService()
        self.uc = self._build()

    def uow(self) -> WorldUoW:
        return WorldUoW(self)

    def _build(self) -> IdentityUseCases:
        c, uow, clock = self.config, self.uow, self.clock
        issuer = SessionIssuer(self.tokens, self.secrets, clock, refresh_ttl_s=c.refresh_ttl_s)
        authn = PasswordAuthenticator(self.hasher, self.limiter, clock, c)
        revocation = FakeRevocation(self.revoked)
        providers = {Provider.YANDEX: self.yandex}
        email = FakeEmail(self.emails)
        codes = CodeChallenges(
            codes=self.codes,
            attempts=Attempts(self),
            limiter=self.limiter,
            secrets=self.secrets,
            clock=clock,
            config=c,
        )
        return IdentityUseCases(
            config=c,
            register=RegisterWithEmail(
                uow,
                hasher=self.hasher,
                limiter=self.limiter,
                email=email,
                codes=codes,
                clock=clock,
                config=c,
            ),
            verify_email=VerifyEmail(uow, clock, c, codes=codes, sessions=issuer),
            login=LoginWithPassword(
                uow, authn, issuer, codes=codes, limiter=self.limiter, email=email, config=c
            ),
            confirm_login=ConfirmLogin(uow, codes=codes, sessions=issuer),
            resend_code=ResendCode(uow, codes=codes, limiter=self.limiter, email=email, config=c),
            request_password_change=RequestPasswordChange(
                uow, authn, codes=codes, limiter=self.limiter, email=email, config=c
            ),
            confirm_password_change=ConfirmPasswordChange(
                uow, self.hasher, revocation, clock, c, email=email, codes=codes
            ),
            admin_login=AdminLogin(uow, authn, issuer),
            refresh=RefreshSession(uow, issuer, revocation, clock),
            logout=Logout(uow, revocation, clock),
            logout_all=LogoutAll(uow, revocation, clock),
            request_password_reset=RequestPasswordReset(
                uow, limiter=self.limiter, email=email, codes=codes, clock=clock, config=c
            ),
            reset_password=ResetPassword(
                uow, self.hasher, revocation, clock, c, email=email, codes=codes, sessions=issuer
            ),
            start_oauth=StartOAuth(
                uow, providers, self.states, self.secrets, c, authenticator=authn
            ),
            complete_oauth=CompleteOAuth(
                uow,
                providers,
                self.states,
                LinkExternalAccount(clock),
                LoginWithLinkedAccount(issuer),
                email=email,
            ),
            unlink=UnlinkExternalAccount(uow, authn, email),
            grant_consents=GrantConsents(uow, clock, c),
            delete_account=DeleteAccount(uow, authn, revocation, clock),
            assign_roles=AssignRoles(uow, clock, revocation),
            update_role_matrix=UpdateRoleMatrix(uow, clock, revocation),
            get_user=GetUser(uow),
            list_users=ListUsers(uow),
            list_linked=ListLinkedAccounts(uow),
            get_role_matrix=GetRoleMatrix(uow),
            get_jwks=GetJwks(FakeKeys()),
            issue_service_token=IssueServiceToken(
                SERVICE_CLIENTS, self.tokens, self.limiter, SERVICE_TOKEN_CONFIG
            ),
            list_verified_emails=ListVerifiedEmails(uow, self.strict_limiter, UserEmailsLimit()),
        )

    async def make_user(
        self,
        nickname: str = "neo_2026",
        email: str = "neo@example.com",
        password: str = "correct horse battery",  # noqa: S107 — тестовый пароль
        *,
        verified: bool = True,
        roles: frozenset[str] | None = None,
    ) -> UUID:
        """Зарегистрировать (и при необходимости подтвердить) пользователя через use-cases."""
        from identity_service.application.dto import RegisterCommand

        challenge = await self.uc.register(
            RegisterCommand(nickname=nickname, email=email, password=password, consents=CONSENTS)
        )
        if verified:
            result = await self.uc.verify_email(
                challenge.challenge_id, self.last_code(), binding_token=challenge.binding_token
            )
            user_id = result.session.user_id
            # Известное устройство DEVICE доверено каждому пользователю из make_user: токен
            # сервер выдаёт сам, поэтому «cookie DEVICE» заводим в данные напрямую.
            now = self.clock.now()
            self.data.devices[(user_id, hash_secret(DEVICE))] = TrustedDevice(
                uuid7(), user_id, hash_secret(DEVICE), now, now + timedelta(days=180)
            )
        else:
            user_id = next(u.id for u in self.data.users.values() if u.email.value == email.lower())
        if roles is not None:
            user = self.data.users[user_id]
            user.roles = roles
        return user_id

    async def login(
        self, login: str, password: str, *, ip: str | None = None, device_token: str | None = DEVICE
    ) -> IssuedSession:
        """Вход с доверенного устройства (по умолчанию) — сразу сессия."""
        result = await self.uc.login(login, password, ip=ip, device_token=device_token)
        assert result.session is not None, "ожидалась сессия, а не code_required"
        return result.session

    async def login_result(
        self, login: str, password: str, *, ip: str | None = None, device_token: str | None = None
    ) -> LoginResult:
        """Вход как есть (по умолчанию — неизвестное устройство)."""
        return await self.uc.login(login, password, ip=ip, device_token=device_token)

    def last_code(self) -> str:
        """Код из последнего письма с кодом."""
        return next(m.code for m in reversed(self.emails) if m.code)

    def codes_sent(self) -> list[str]:
        """Все отправленные коды по порядку."""
        return [m.code for m in self.emails if m.code]

    def challenge(self, challenge_id: UUID) -> EmailChallenge:
        """Запись кода с учётом списанных попыток."""
        ch = self.data.challenges[challenge_id]
        return dataclasses.replace(ch, attempts=ch.attempts + self.spent.get(ch.id, 0))

    def advance(self, seconds: int) -> None:
        """Сдвинуть часы."""
        self.clock.advance(seconds=seconds)


class WorldUoW(FakeUnitOfWork):
    def __init__(self, world: World) -> None:
        super().__init__()
        self._world = world
        self._data = copy.deepcopy(world.data)
        self.users = Users(self._data)
        self.credentials = Creds(self._data)
        self.sessions = Sessions(self._data)
        self.identities = Identities(self._data)
        self.roles = Roles(self._data)
        self.consents = Consents(self._data)
        self.email_challenges = Challenges(self._data, world.spent, world.spent_total)
        self.trusted_devices = Devices(self._data)
        self.idempotency = world.idempotency

    async def __aenter__(self) -> Self:
        await super().__aenter__()
        return self

    async def commit(self) -> None:
        await super().commit()
        self._world.data = self._data
        self._world.events.extend(self.events)


class FakeHasher:
    def __init__(self) -> None:
        self.dummy_calls = 0
        self.verify_calls = 0

    async def hash(self, password: str) -> str:
        return f"h${password}"

    async def verify(self, password_hash: str, password: str) -> bool:
        self.verify_calls += 1
        await asyncio.sleep(0)  # как настоящий хэшер: управление уходит в event loop
        return password_hash.split("$", 1)[1] == password

    def needs_rehash(self, password_hash: str) -> bool:
        return not password_hash.startswith("h$")

    async def verify_dummy(self, password: str) -> None:
        self.dummy_calls += 1


class FakeTokens:
    def __init__(self) -> None:
        self.service_issued: list[tuple[str, str, frozenset[str], int]] = []

    def issue_access(
        self, *, user_id: UUID, session_id: UUID, roles: Iterable[str], perms: Iterable[str]
    ) -> IssuedAccess:
        return IssuedAccess(f"access:{user_id}:{session_id}:{','.join(perms)}", 900)

    def issue_service(
        self, *, client_id: str, audience: str, scopes: Iterable[str], ttl_s: int
    ) -> IssuedServiceToken:
        self.service_issued.append((client_id, audience, frozenset(scopes), ttl_s))
        n = len(self.service_issued)
        return IssuedServiceToken(f"svc:{client_id}:{audience}:{n}", ttl_s, f"jti-{n}")


GAME_SECRET = "game-secret-value"
SERVICE_CLIENTS = ServiceClientRegistry(
    [
        ServiceClient.create(
            "game",
            hashlib.sha256(GAME_SECRET.encode()).hexdigest(),
            {"media_pipeline": ["assets.link"]},
        )
    ]
)
SERVICE_TOKEN_CONFIG = ServiceTokenConfig(
    ttl_s=600, failures_per_client_ip_per_min=3, success_per_client_per_min=5
)


class FakeKeys:
    def jwks(self) -> dict[str, Any]:
        return {"keys": [{"kid": "k1"}, {"kid": "k2"}]}


class SeqSecrets:
    def __init__(self) -> None:
        self.n = 0

    def token(self, nbytes: int) -> str:
        self.n += 1
        return f"secret{self.n}x{nbytes}"


class FakeLimiter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, *, limit: int, window_s: int) -> RateDecision:
        self.counts[key] = self.counts.get(key, 0) + 1
        return RateDecision(self.counts[key] <= limit, window_s)

    async def forgive(self, key: str) -> None:
        self.counts[key] = max(self.counts.get(key, 0) - 1, 0)

    async def reset(self, key: str) -> None:
        self.counts.pop(key, None)


class FakeStrictLimiter:
    """Лимитер fail-closed: ``broken`` имитирует недоступный Redis."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.broken = False

    async def hit(self, key: str, *, limit: int, window_s: int, amount: int = 1) -> RateDecision:
        if self.broken:
            raise DependencyUnavailableError("лимитер недоступен", details={"retry_after": 5})
        self.counts[key] = self.counts.get(key, 0) + amount
        return RateDecision(self.counts[key] <= limit, window_s)


class FakeRevocation:
    def __init__(self, revoked: set[UUID]) -> None:
        self.revoked = revoked

    async def revoke(self, session_ids: Iterable[UUID]) -> None:
        self.revoked.update(session_ids)


class FakeStateStore:
    def __init__(self) -> None:
        self.items: dict[str, OAuthState] = {}

    async def put(self, key: str, state: OAuthState, ttl_s: int) -> None:
        self.items[key] = state

    async def pop(self, key: str) -> OAuthState | None:
        return self.items.pop(key, None)


class FakeOAuth:
    provider = Provider.YANDEX

    def __init__(self) -> None:
        self.profiles: dict[str, ExternalProfile] = {}
        self.verifiers: dict[str, str] = {}
        self.last_challenge = ""

    def authorize_url(self, *, state: str, code_challenge: str) -> str:
        self.last_challenge = code_challenge
        return f"https://oauth.test/authorize?state={state}&code_challenge={code_challenge}"

    def will_return(self, code: str, subject: str, display: str = "ya_user") -> None:
        self.profiles[code] = ExternalProfile(subject, display)

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        self.verifiers[code] = code_verifier
        return f"tok-{code}"

    async def fetch_profile(self, access_token: str) -> ExternalProfile:
        return self.profiles[access_token.removeprefix("tok-")]


class FakeEmail:
    def __init__(self, sink: list[EmailMessage]) -> None:
        self.sink = sink

    async def send(self, message: EmailMessage) -> None:
        self.sink.append(message)


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def make_world() -> Callable[..., World]:
    """Мир с переопределёнными лимитами: ``make_world(login_per_account=7)``."""

    def factory(**limits: Any) -> World:
        base = World().config
        return World(dataclasses.replace(base, limits=LimitConfig(**limits)))

    return factory
