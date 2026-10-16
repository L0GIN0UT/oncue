"""Адаптеры identity на реальных Postgres и Redis, криптография и клиент Яндекса."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from identity_service.application.ports import EmailToken, EmailTokenPurpose, OAuthState
from identity_service.application.security import OAuthStateError
from identity_service.domain import (
    AuthIdentity,
    DisplayName,
    EmailAddress,
    Nickname,
    Provider,
    RefreshToken,
    Session,
    SessionKind,
    User,
)
from identity_service.infrastructure.clients import LogEmailSender, YandexOAuthProvider
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
    StaticKeyProvider,
    parse_signing_keys,
)
from identity_service.main import DEFINITION
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from structlog.testing import capture_logs

from gv_auth import JwtVerifier, RedisRevocationChecker
from gv_core import ConflictError, DependencyUnavailableError
from gv_core.ids import uuid7
from gv_testkit import FakeClock, InfraProvider, apply_migrations, outbox_envelopes

pytestmark = pytest.mark.integration


# ----------------------------------------------------------------------------- Postgres


@pytest.fixture
async def sessions(database_url: str) -> AsyncIterator[async_sessionmaker[Any]]:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    engine = create_async_engine(database_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _uow(sessions: async_sessionmaker[Any], clock: FakeClock) -> SqlIdentityUnitOfWork:
    return SqlIdentityUnitOfWork(sessions, producer="identity", clock=clock)


def _user(nick: str = "Neo_2026", email: str = "neo@example.com", now: Any = None) -> User:
    return User.register(
        uuid7(),
        nickname=Nickname.parse(nick),
        email=EmailAddress.parse(email),
        display_name=DisplayName.parse("Neo"),
        locale="ru",
        roles=frozenset({"user"}),
        now=now,
    )


async def test_seeded_matrix(sessions: async_sessionmaker[Any], fake_clock: FakeClock) -> None:
    async with _uow(sessions, fake_clock) as uow:
        matrix = await uow.roles.get_matrix()
    assert set(matrix.roles) == {"user", "moderator", "admin"}
    assert matrix.roles["admin"] == matrix.permissions
    # 9 этапа 1 + 5 прав game (0005, этап 2 §2.7) + 4 права сборников (0007, этап 3b §2.8)
    assert len(matrix.permissions) == 18
    assert matrix.roles["moderator"] == {
        "admin.access",
        "users.view",
        "catalog.block_item",
        "catalog.edit_aliases",
        "config.modes.view",
        "game.daily.view",
        "game.daily.curate",
        "collections.view",
        "collections.unpublish",
    }


async def test_user_roundtrip_uniqueness_and_outbox(
    sessions: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    user = _user(now=fake_clock.now())
    async with _uow(sessions, fake_clock) as uow:
        await uow.users.add(user)
        uow.track(user)
        await uow.commit()

    async with _uow(sessions, fake_clock) as uow:
        by_nick = await uow.users.find_by_nickname(Nickname.parse("NEO_2026"))
        by_mail = await uow.users.find_by_email(EmailAddress.parse("NEO@example.com"))
        assert by_nick is not None and by_mail is not None
        assert by_nick.id == by_mail.id == user.id
        assert by_nick.roles == {"user"}
        assert by_nick.version == 1

    for nick, email, code in [
        ("neo_2026", "other@example.com", "user.nickname_taken"),
        ("other", "neo@example.com", "user.email_taken"),
    ]:
        async with _uow(sessions, fake_clock) as uow:
            with pytest.raises(ConflictError) as info:
                await uow.users.add(_user(nick, email, fake_clock.now()))
            assert info.value.code == code

    async with sessions() as s:
        (envelope,) = await outbox_envelopes(s, DEFINITION.tables.outbox)  # type: ignore[union-attr]
    assert envelope.event_type == "identity.UserRegistered"
    assert envelope.topic == "identity.user.v1"
    assert "neo@example.com" not in envelope.to_bytes().decode()


async def test_optimistic_lock(sessions: async_sessionmaker[Any], fake_clock: FakeClock) -> None:
    user = _user(now=fake_clock.now())
    async with _uow(sessions, fake_clock) as uow:
        await uow.users.add(user)
        await uow.commit()
    async with _uow(sessions, fake_clock) as a, _uow(sessions, fake_clock) as b:
        ua, ub = await a.users.get(user.id), await b.users.get(user.id)
        assert ua is not None and ub is not None
        ua.link_provider(Provider.YANDEX)
        await a.users.save(ua)
        await a.commit()
        ub.link_provider(Provider.YANDEX)
        with pytest.raises(ConflictError):
            await b.users.save(ub)


async def test_sessions_tokens_identities(
    sessions: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    now = fake_clock.now()
    user, other = _user(now=now), _user("trinity", "t@example.com", now)
    session = Session(id=uuid7(), user_id=user.id, kind=SessionKind.WEB, created_at=now)
    token = RefreshToken(
        id=uuid7(),
        session_id=session.id,
        token_hash="a" * 64,
        parent_id=None,
        created_at=now,
        expires_at=now + timedelta(days=1),
    )
    async with _uow(sessions, fake_clock) as uow:
        await uow.users.add(user)
        await uow.users.add(other)
        await uow.sessions.add(session)
        await uow.sessions.add_refresh(token)
        await uow.identities.add(AuthIdentity(user.id, Provider.YANDEX, "ya-1", "neo", now))
        await uow.email_tokens.add(
            EmailToken(
                uuid7(), user.id, EmailTokenPurpose.VERIFY, "b" * 64, now, now + timedelta(hours=1)
            )
        )
        await uow.commit()

    async with _uow(sessions, fake_clock) as uow:
        found = await uow.sessions.find_refresh_for_update("a" * 64)
        assert found is not None and found.session_id == session.id
        found.used_at = now
        await uow.sessions.save_refresh(found)
        loaded = await uow.sessions.get(session.id)
        assert loaded is not None
        loaded.revoke(now, "logout")
        await uow.sessions.save(loaded)
        await uow.commit()
    async with _uow(sessions, fake_clock) as uow:
        again = await uow.sessions.find_refresh_for_update("a" * 64)
        assert again is not None and again.used_at == now
        assert await uow.sessions.active_for_user(user.id) == []
        # Токен из письма гасится ровно один раз и не другой целью.
        assert await uow.email_tokens.consume("b" * 64, EmailTokenPurpose.RESET, now) is None
        assert await uow.email_tokens.consume("b" * 64, EmailTokenPurpose.VERIFY, now) == user.id
        assert await uow.email_tokens.consume("b" * 64, EmailTokenPurpose.VERIFY, now) is None
        # Погашение всех непогашенных токенов пользователя одной цели (M3).
        for n, purpose in enumerate([EmailTokenPurpose.RESET, EmailTokenPurpose.RESET]):
            await uow.email_tokens.add(
                EmailToken(uuid7(), user.id, purpose, f"{n}" * 64, now, now + timedelta(hours=1))
            )
        await uow.email_tokens.add(
            EmailToken(
                uuid7(), other.id, EmailTokenPurpose.RESET, "9" * 64, now, now + timedelta(hours=1)
            )
        )
        assert (
            await uow.email_tokens.invalidate_for_user(user.id, EmailTokenPurpose.RESET, now) == 2
        )
        assert await uow.email_tokens.consume("0" * 64, EmailTokenPurpose.RESET, now) is None
        assert await uow.email_tokens.consume("9" * 64, EmailTokenPurpose.RESET, now) == other.id
        await uow.commit()

    async with _uow(sessions, fake_clock) as uow:
        with pytest.raises(ConflictError) as info:
            await uow.identities.add(AuthIdentity(other.id, Provider.YANDEX, "ya-1", None, now))
        assert info.value.code == "auth.provider_account_taken"
        # Транзакция жива после конфликта (savepoint): можно продолжать.
        assert (await uow.identities.get(Provider.YANDEX, "ya-1")).user_id == user.id  # type: ignore[union-attr]
        assert await uow.identities.remove(user.id, Provider.YANDEX)
        assert not await uow.identities.remove(user.id, Provider.YANDEX)


async def test_search_and_matrix_save(
    sessions: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    now = fake_clock.now()
    async with _uow(sessions, fake_clock) as uow:
        for i in range(3):
            await uow.users.add(_user(f"user_{i}", f"u{i}@example.com", now))
        await uow.commit()
    async with _uow(sessions, fake_clock) as uow:
        page = await uow.users.search("user_", after=None, limit=2)
        assert [u.nickname.value for u in page] == ["user_0", "user_1"]
        rest = await uow.users.search("user_", after=page[-1].id, limit=10)
        assert [u.nickname.value for u in rest] == ["user_2"]
        assert await uow.users.search("U1@", after=None, limit=10)
        assert await uow.users.search("%", after=None, limit=10) == []  # LIKE экранирован
        matrix = await uow.roles.get_matrix()
        updated = matrix.replace({**matrix.roles, "helper": frozenset({"users.view"})})
        await uow.roles.save_matrix(updated)
        await uow.commit()
    async with _uow(sessions, fake_clock) as uow:
        assert (await uow.roles.get_matrix()).roles["helper"] == {"users.view"}


async def test_idempotency_keys_in_identity_transaction(
    sessions: async_sessionmaker[Any], fake_clock: FakeClock
) -> None:
    actor = uuid7()
    async with _uow(sessions, fake_clock) as uow:
        await uow.idempotency.save(actor, "key-0001", "a" * 64, {"ok": True})
        await uow.rollback()
    async with _uow(sessions, fake_clock) as uow:
        assert await uow.idempotency.find(actor, "key-0001") is None  # откат — ключа нет
        await uow.idempotency.save(actor, "key-0001", "a" * 64, {"ok": True})
        await uow.commit()
    async with _uow(sessions, fake_clock) as uow:
        found = await uow.idempotency.find(actor, "key-0001")
        assert found is not None and found.response == {"ok": True}


# ----------------------------------------------------------------------------- Redis


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[tuple[Redis, str]]:
    client = Redis.from_url(infra.redis_url())
    prefix = f"test:{uuid7().hex[-12:]}:"
    yield client, prefix
    keys = [k async for k in client.scan_iter(match=f"{prefix}*")]
    if keys:
        await client.delete(*keys)
    await client.aclose()


async def test_rate_limiter(redis: tuple[Redis, str]) -> None:
    client, prefix = redis
    limiter = RedisRateLimiter(client, prefix=prefix)
    assert (await limiter.hit("k", limit=2, window_s=60)).allowed
    assert (await limiter.hit("k", limit=2, window_s=60)).allowed
    denied = await limiter.hit("k", limit=2, window_s=60)
    assert not denied.allowed
    assert 1 <= denied.retry_after_s <= 60
    # forgive возвращает попытку и сохраняет окно (TTL).
    await limiter.forgive("k")
    assert int(await client.get(f"{prefix}k")) == 2  # type: ignore[arg-type]
    assert 0 < await client.ttl(f"{prefix}k") <= 60
    await limiter.reset("k")
    assert await client.get(f"{prefix}k") is None
    # Прощать нечего — ключ не создаётся и не уходит в минус.
    await limiter.forgive("k")
    assert await client.get(f"{prefix}k") is None


async def test_rate_limiter_hit_is_atomic_under_concurrency(redis: tuple[Redis, str]) -> None:
    client, prefix = redis
    limiter = RedisRateLimiter(client, prefix=prefix)
    decisions = await asyncio.gather(
        *(limiter.hit("race", limit=5, window_s=60) for _ in range(30))
    )
    assert sum(d.allowed for d in decisions) == 5


async def test_rate_limiter_fails_open() -> None:
    broken = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
    limiter = RedisRateLimiter(broken)
    assert (await limiter.hit("k", limit=1, window_s=60)).allowed
    await limiter.forgive("k")
    await limiter.reset("k")
    await broken.aclose()


async def test_revocation_store_is_read_by_gv_auth_checker(redis: tuple[Redis, str]) -> None:
    client, prefix = redis
    store = RedisRevocationStore(client, ttl_s=900, prefix=prefix)
    checker = RedisRevocationChecker(client, prefix=prefix)
    sid = uuid7()
    assert not await checker.is_revoked(str(sid))
    await store.revoke([sid])
    assert await checker.is_revoked(str(sid))
    ttl = await client.ttl(f"{prefix}auth:revoked_sid:{sid}")
    assert 0 < ttl <= 900


async def test_oauth_state_is_single_use(redis: tuple[Redis, str]) -> None:
    client, prefix = redis
    store = RedisOAuthStateStore(client, prefix=prefix)
    state = OAuthState("link", Provider.YANDEX, uuid7(), uuid7(), "verifier", "binding", "/p")
    await store.put("h1", state, 60)
    assert 0 < await client.ttl(f"{prefix}oauth:state:h1") <= 60
    assert await store.pop("h1") == state
    assert await store.pop("h1") is None


# ----------------------------------------------------------------------------- криптография


async def test_argon2id_parameters_and_rehash() -> None:
    hasher = Argon2Hasher(time_cost=3, memory_kib=65_536)
    digest = await hasher.hash("correct horse battery")
    assert digest.startswith("$argon2id$")
    assert "m=65536,t=3" in digest
    assert await hasher.verify(digest, "correct horse battery")
    assert not await hasher.verify(digest, "wrong")
    assert not await hasher.verify("garbage", "x")
    assert not hasher.needs_rehash(digest)
    assert Argon2Hasher(time_cost=4, memory_kib=65_536).needs_rehash(digest)
    await hasher.verify_dummy("anything")


async def test_argon2_concurrency_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    hasher = Argon2Hasher(time_cost=1, memory_kib=1024, max_concurrency=2)
    digest = await hasher.hash("pw")
    lock = threading.Lock()
    state = {"now": 0, "peak": 0}
    original = hasher._verify_sync

    def tracked(password_hash: str, password: str) -> bool:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.02)  # держим слот, чтобы параллельные вызовы пересеклись
        try:
            return original(password_hash, password)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(hasher, "_verify_sync", tracked)
    results = await asyncio.gather(*(hasher.verify(digest, "pw") for _ in range(8)))
    assert all(results)
    assert state["peak"] == 2


def _pem(key: Any) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


async def test_jwt_issue_verify_and_rotation(fake_clock: FakeClock) -> None:
    old, new = ed25519.Ed25519PrivateKey.generate(), ec.generate_private_key(ec.SECP256R1())
    raw = json.dumps(
        [
            {
                "kid": "k-old",
                "private_pem": _pem(old),
                "active": False,
                "not_after": (fake_clock.now() + timedelta(hours=1)).isoformat(),
            },
            {"kid": "k-new", "private_pem": _pem(new), "active": True},
        ]
    )
    keys = StaticKeyProvider(parse_signing_keys(raw), fake_clock)
    assert [k["kid"] for k in keys.jwks()["keys"]] == ["k-old", "k-new"]
    assert all("d" not in k for k in keys.jwks()["keys"])  # только публичные части
    issuer = JwtIssuer(keys, _RealClock(), issuer="https://id", audience="gv", access_ttl_s=900)
    user, sid = uuid.uuid4(), uuid.uuid4()
    issued = issuer.issue_access(user_id=user, session_id=sid, roles=["user"], perms=["a.b"])

    async def load() -> dict[str, Any]:
        return keys.jwks()

    principal = await JwtVerifier(load, issuer="https://id", audience="gv").verify(issued.token)
    assert principal.user_id == user
    assert principal.session_id == str(sid)
    assert principal.plan == "free_all"
    fake_clock.advance(hours=2)
    assert [k["kid"] for k in keys.jwks()["keys"]] == ["k-new"]  # старый истёк


class _RealClock:
    def now(self) -> Any:
        from datetime import UTC, datetime

        return datetime.now(UTC)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '[{"kid": "a", "private_pem": "bad", "active": true}]',
        json.dumps([{"kid": "a", "private_pem": _pem(ed25519.Ed25519PrivateKey.generate())}]),
        json.dumps(
            [
                {
                    "kid": "a",
                    "private_pem": _pem(ec.generate_private_key(ec.SECP384R1())),
                    "active": True,
                }
            ]
        ),
    ],
)
def test_bad_key_config(raw: str) -> None:
    with pytest.raises(KeyConfigError) as info:
        parse_signing_keys(raw)
    assert "PRIVATE KEY" not in str(info.value)


# ----------------------------------------------------------------------------- Яндекс


def _yandex() -> YandexOAuthProvider:
    return YandexOAuthProvider(
        client_id="cid", client_secret="csecret", redirect_uri="https://gv.test/cb"
    )


async def test_yandex_authorize_url_has_pkce_and_fixed_redirect() -> None:
    url = httpx.URL(_yandex().authorize_url(state="st", code_challenge="ch"))
    assert url.host == "oauth.yandex.ru"
    assert url.params["code_challenge_method"] == "S256"
    assert url.params["code_challenge"] == "ch"
    assert url.params["redirect_uri"] == "https://gv.test/cb"
    assert url.params["state"] == "st"


async def test_yandex_exchange_and_profile() -> None:
    with respx.mock(assert_all_called=True) as mock:
        token_route = mock.post("https://oauth.yandex.ru/token").respond(
            200, json={"access_token": "ya-token"}
        )
        mock.get("https://login.yandex.ru/info").respond(
            200, json={"id": "12345", "login": "neo", "default_email": "neo@yandex.ru"}
        )
        provider = _yandex()
        token = await provider.exchange_code(code="c", code_verifier="v" * 64)
        profile = await provider.fetch_profile(token)
    sent = dict(httpx.QueryParams(token_route.calls.last.request.content.decode()))
    assert sent["code_verifier"] == "v" * 64
    assert sent["grant_type"] == "authorization_code"
    assert profile.subject == "12345"
    assert profile.display == "neo"


async def test_yandex_errors() -> None:
    with respx.mock() as mock:
        mock.post("https://oauth.yandex.ru/token").respond(400, json={"error": "invalid_grant"})
        with pytest.raises(OAuthStateError):
            await _yandex().exchange_code(code="c", code_verifier="v")
    with respx.mock() as mock:
        mock.post("https://oauth.yandex.ru/token").respond(502)
        with pytest.raises(DependencyUnavailableError):
            await _yandex().exchange_code(code="c", code_verifier="v")
    with respx.mock() as mock:
        mock.post("https://oauth.yandex.ru/token").mock(side_effect=httpx.ConnectError("x"))
        with pytest.raises(DependencyUnavailableError):
            await _yandex().exchange_code(code="c", code_verifier="v")


async def test_log_email_sender_hides_link_in_production() -> None:
    from identity_service.application.ports import EmailMessage

    message = EmailMessage(
        EmailAddress.parse("neo@example.com"), "verify", "https://x/?token=SECRET"
    )
    with capture_logs() as logs:
        await LogEmailSender(include_links=False).send(message)
        await LogEmailSender(include_links=True).send(message)
    assert "SECRET" not in str(logs[0])
    assert "SECRET" in str(logs[1])
    assert "neo@example.com" not in str(logs)  # получатель не логируется никогда
