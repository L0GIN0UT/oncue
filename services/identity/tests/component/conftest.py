"""Сервис identity целиком: реальные Postgres и Redis, Яндекс — заглушка respx."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from identity_service.main import DEFINITION
from identity_service.settings import Settings
from pydantic import SecretStr
from redis.asyncio import Redis
from structlog.testing import capture_logs

from gv_auth import ACCESS_COOKIE, CSRF_COOKIE, CSRF_HEADER
from gv_core.ids import uuid7
from gv_messaging import EventEnvelope
from gv_service import Infrastructure, create_app
from gv_testkit import InfraProvider, apply_migrations, outbox_envelopes, service_client

ORIGIN = "https://gv.test"
REFRESH_COOKIE = "gv_refresh"
PASSWORD = "correct horse battery"
YA_TOKEN = "https://ya.test/token"
YA_INFO = "https://ya.test/info"
# Клиенты сервисных токенов (D13); в настройках identity — только sha256 секретов.
ADMIN_SECRET = "admin-client-secret-0123456789abcdef"
GAME_SECRET = "game-client-secret-0123456789abcdef"
NOTIFICATIONS_SECRET = "notifications-client-secret-0123456789abcdef"
# Секрет connection-токенов Centrifugo (D9 этапа 4).
REALTIME_SECRET = "realtime-connection-secret-0123456789abcdef"


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


SERVICE_CLIENTS = (
    {
        "client_id": "admin",
        "secret_sha256": sha256_hex(ADMIN_SECRET),
        "audiences": {"identity": ["admin_sessions"]},
    },
    {
        "client_id": "game",
        "secret_sha256": sha256_hex(GAME_SECRET),
        "audiences": {"media_pipeline": ["assets.link"]},
    },
    {
        "client_id": "notifications",
        "secret_sha256": sha256_hex(NOTIFICATIONS_SECRET),
        "audiences": {"identity": ["users.emails.read", "users.active.list"]},
    },
)


def pem() -> str:
    return (
        ed25519.Ed25519PrivateKey.generate()
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


@dataclass
class Service:
    """Клиент, инфраструктура и перехваченные логи."""

    client: httpx.AsyncClient
    infra: Infrastructure
    logs: list[dict[str, Any]]
    yandex: respx.MockRouter
    prefix: str
    admin_client: dict[str, str] = field(default_factory=dict)
    """Заголовок с сервисным токеном admin (internal-ручки admin-сессий)."""
    app: Any = None
    """ASGI-приложение (для клиентов с другим IP источника)."""

    def client_from(self, ip: str) -> httpx.AsyncClient:
        """Клиент к тому же приложению, запросы которого приходят с адреса ``ip``."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app, client=(ip, 40000)),
            base_url=str(self.client.base_url),
        )

    def csrf(self) -> dict[str, str]:
        """Заголовки браузерной мутации с cookie-аутентификацией."""
        return {"Origin": ORIGIN, CSRF_HEADER: self.client.cookies.get(CSRF_COOKIE) or ""}

    def forget_session(self) -> None:
        """Убрать cookie сессии, оставив доверенное устройство (``gv_device``)."""
        names = {ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE}
        jar = self.client.cookies.jar
        for cookie in [c for c in jar if c.name in names]:
            jar.clear(cookie.domain, cookie.path, cookie.name)

    def code_for(self, challenge_id: str) -> str:
        """Код из письма по ``challenge_id`` (backend=log пишет его в лог только в local/ci)."""
        codes = [
            str(e["code"])
            for e in self.logs
            if e.get("event") == "email.logged"
            and e.get("challenge_id") == challenge_id
            and "code" in e
        ]
        assert codes, f"письма с кодом для {challenge_id} нет"
        return codes[-1]

    async def events(self) -> list[EventEnvelope]:
        assert self.infra.sessions is not None and DEFINITION.tables is not None
        async with self.infra.sessions() as session:
            return await outbox_envelopes(session, DEFINITION.tables.outbox)

    async def register(
        self, nickname: str = "neo_2026", email: str = "neo@example.com", *, verify: bool = True
    ) -> dict[str, Any]:
        """Зарегистрироваться; с ``verify`` ещё и ввести код (сессия и gv_device — в cookie)."""
        response = await self.client.post(
            "/v1/auth/register",
            json={
                "nickname": nickname,
                "email": email,
                "password": PASSWORD,
                "consents": [
                    {"kind": "terms", "version": "2026-10"},
                    {"kind": "privacy", "version": "2026-10"},
                ],
            },
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 202, response.text
        challenge: dict[str, Any] = response.json()
        if verify:
            ok = await self.client.post(
                "/v1/auth/verify-email",
                json={
                    "challenge_id": challenge["challenge_id"],
                    "code": self.code_for(challenge["challenge_id"]),
                },
                headers={"Origin": ORIGIN},
            )
            assert ok.status_code == 200, ok.text
        return challenge

    async def login(self, login: str = "neo_2026", password: str = PASSWORD) -> httpx.Response:
        return await self.client.post(
            "/v1/auth/login",
            json={"login": login, "password": password},
            headers={"Origin": ORIGIN},
        )


async def service_token_headers(
    client: httpx.AsyncClient, client_id: str, secret: str, audience: str = "identity"
) -> dict[str, str]:
    """Получить сервисный токен у identity и вернуть заголовок Authorization."""
    response = await client.post(
        "/internal/v1/service-tokens",
        json={"client_id": client_id, "client_secret": secret, "audience": audience},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_settings(database_url: str, redis_url: str, prefix: str, **overrides: Any) -> Settings:
    keys = [
        {"kid": "k-old", "private_pem": pem(), "active": False},
        {"kid": "k-new", "private_pem": pem(), "active": True},
    ]
    values: dict[str, Any] = {
        "environment": "ci",
        "database_url": SecretStr(database_url),
        "redis_url": SecretStr(redis_url),
        "public_origins": (ORIGIN,),
        "jwt_issuer": "https://identity.test",
        "jwt_audience": "guessverse",
        "jwt": {"signing_keys": SecretStr(json.dumps(keys))},
        "cookies": {"secure": False, "refresh_path": "/v1/auth", "oauth_path": "/v1/oauth"},
        "yandex": {
            "enabled": True,
            "client_id": "cid",
            "client_secret": SecretStr("csecret"),
            "redirect_uri": "https://gv.test/api/identity/v1/oauth/yandex/callback",
            "token_url": YA_TOKEN,
            "info_url": YA_INFO,
        },
        # Дешёвые параметры argon2 только ради скорости тестов.
        "argon2": {"time_cost": 1, "memory_kib": 1024},
        "consents": {"required": "terms:2026-10,privacy:2026-10"},
        "oauth": {"return_to_prefixes": ("/profile", "/play", "/")},
        "bootstrap_admin_emails": ("boss@example.com",),
        "redis_key_prefix": prefix,
        "service_clients": SERVICE_CLIENTS,
        "realtime": {
            "api_url": "http://centrifugo.test/api",
            "api_key": SecretStr("centrifugo-api-key-0123456789abcdef"),
            "connection_token_secret": SecretStr(REALTIME_SECRET),
        },
        "realtime_token_limit": {"per_user": 5, "window_s": 60},
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
async def migrated_db(database_url: str) -> str:
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    return database_url


@dataclass
class Seeding:
    """Хелпер тестов bootstrap-админа: сборка настроек, прогон bootstrap, admin-вход по HTTP."""

    database_url: str
    _infra: InfraProvider

    def settings(self, **overrides: Any) -> tuple[Settings, str]:
        """Настройки identity (окружение ci по умолчанию) и префикс ключей Redis."""
        prefix = f"test:{uuid7().hex[-12:]}:"
        return make_settings(
            self.database_url, self._infra.redis_url(), prefix, **overrides
        ), prefix

    @asynccontextmanager
    async def app(
        self, settings: Settings, prefix: str
    ) -> AsyncIterator[tuple[httpx.AsyncClient, dict[str, str]]]:
        """Поднять сервис на этих настройках: клиент и заголовок сервисного токена admin."""
        runtime = await Infrastructure.open(settings, DEFINITION.tables)
        try:
            app = create_app(DEFINITION, runtime)
            async with service_client(app) as client:
                headers = await service_token_headers(client, "admin", ADMIN_SECRET)
                yield client, headers
        finally:
            redis = Redis.from_url(self._infra.redis_url())
            keys = [k async for k in redis.scan_iter(match=f"{prefix}*")]
            if keys:
                await redis.delete(*keys)
            await redis.aclose()
            await runtime.close()


@pytest.fixture
def seeding(migrated_db: str, infra: InfraProvider) -> Seeding:
    """Фикстура для тестов bootstrap-админа (миграции применены, адреса из ``infra``)."""
    return Seeding(migrated_db, infra)


@pytest.fixture
async def service(migrated_db: str, infra: InfraProvider) -> AsyncIterator[Service]:
    prefix = f"test:{uuid7().hex[-12:]}:"
    settings = make_settings(migrated_db, infra.redis_url(), prefix)
    runtime = await Infrastructure.open(settings, DEFINITION.tables)
    try:
        with (
            capture_logs() as logs,
            respx.mock(assert_all_called=False) as yandex,
        ):
            app = create_app(DEFINITION, runtime)
            async with service_client(app) as client:
                service = Service(client, runtime, logs, yandex, prefix, app=app)
                service.admin_client = await service_token_headers(client, "admin", ADMIN_SECRET)
                yield service
    finally:
        redis = Redis.from_url(infra.redis_url())
        keys = [k async for k in redis.scan_iter(match=f"{prefix}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await runtime.close()
