"""Bootstrap-админ автосидинга (только local/ci): создание, идемпотентность, запрет в prod.

Проверяется весь путь автосидинга: команда ``migrate`` (``ensure_bootstrap_admin``) создаёт
админа, после чего ``POST /internal/v1/admin-sessions`` отдаёт admin-токен.
"""

from __future__ import annotations

from typing import Any

import httpx
import jwt
import pytest
from identity_service.infrastructure.security import KeyConfigError
from identity_service.wiring import ensure_bootstrap_admin
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.component

LOGIN = "seed-admin@example.com"
PASSWORD = "seed horse battery staple"  # проходит политику (длина ≥ 10, не из топ-10k)


def _claims(access_token: str) -> dict[str, Any]:
    # Подпись здесь не проверяем: интересны роли и права внутри выданного токена.
    return jwt.decode(access_token, options={"verify_signature": False})


async def _admin_login(client: httpx.AsyncClient, headers: dict[str, str]) -> httpx.Response:
    return await client.post(
        "/internal/v1/admin-sessions",
        headers=headers,
        json={"login": LOGIN, "password": PASSWORD},
    )


async def _count_users(database_url: str, email: str) -> int:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            value = await conn.scalar(
                text("SELECT count(*) FROM users WHERE email = :email"), {"email": email}
            )
            return int(value or 0)
    finally:
        await engine.dispose()


async def test_bootstrap_creates_admin_that_can_login(seeding: Any) -> None:
    """В ci заданные creds → создаётся админ, который входит и имеет admin.access."""
    settings, prefix = seeding.settings(
        bootstrap_admin={"login": LOGIN, "password": SecretStr(PASSWORD)}
    )
    await ensure_bootstrap_admin(settings)

    async with seeding.app(settings, prefix) as (client, headers):
        response = await _admin_login(client, headers)
    assert response.status_code == 200, response.text
    claims = _claims(response.json()["access_token"])
    assert "admin" in claims["roles"]
    assert "admin.access" in claims["perms"]


async def test_bootstrap_is_idempotent(seeding: Any) -> None:
    """Повторный прогон не создаёт второго админа и не ломает admin-вход."""
    settings, prefix = seeding.settings(
        bootstrap_admin={"login": LOGIN, "password": SecretStr(PASSWORD)}
    )
    await ensure_bootstrap_admin(settings)
    await ensure_bootstrap_admin(settings)

    assert await _count_users(seeding.database_url, LOGIN) == 1
    async with seeding.app(settings, prefix) as (client, headers):
        response = await _admin_login(client, headers)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("environment", ["production", "staging"])
async def test_bootstrap_forbidden_outside_local_ci(seeding: Any, environment: str) -> None:
    """Заданные creds в staging/production — ошибка старта; админ не создаётся."""
    settings, _ = seeding.settings(
        environment=environment,
        bootstrap_admin={"login": LOGIN, "password": SecretStr(PASSWORD)},
    )
    with pytest.raises(KeyConfigError):
        await ensure_bootstrap_admin(settings)
    assert await _count_users(seeding.database_url, LOGIN) == 0


async def test_bootstrap_noop_without_creds(seeding: Any) -> None:
    """Без creds — ничего не создаётся."""
    settings, _ = seeding.settings()
    await ensure_bootstrap_admin(settings)
    assert await _count_users(seeding.database_url, LOGIN) == 0


async def test_bootstrap_partial_config_is_error(seeding: Any) -> None:
    """Половинная настройка (только login, без password) — ошибка, не тихое отключение."""
    settings, _ = seeding.settings(bootstrap_admin={"login": LOGIN})
    with pytest.raises(KeyConfigError):
        await ensure_bootstrap_admin(settings)
    assert await _count_users(seeding.database_url, LOGIN) == 0


async def _clear_password(database_url: str, email: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM password_credentials WHERE user_id = "
                    "(SELECT id FROM users WHERE email = :email)"
                ),
                {"email": email},
            )
    finally:
        await engine.dispose()


async def test_bootstrap_does_not_set_password_on_existing(seeding: Any) -> None:
    """Существующему аккаунту без пароля bootstrap не подставляет пароль (защита от захвата)."""
    settings, prefix = seeding.settings(
        bootstrap_admin={"login": LOGIN, "password": SecretStr(PASSWORD)}
    )
    await ensure_bootstrap_admin(settings)
    # Имитируем чужой/беспарольный аккаунт с тем же e-mail: убираем хэш пароля.
    await _clear_password(seeding.database_url, LOGIN)
    await ensure_bootstrap_admin(settings)  # повтор не должен задать пароль заново

    async with seeding.app(settings, prefix) as (client, headers):
        response = await _admin_login(client, headers)
    assert response.status_code != 200, "пароль существующему аккаунту не восстанавливается"


async def _roles_events(database_url: str) -> list[dict[str, Any]]:
    """Payload-ы ``UserRolesChanged`` из outbox (по порядку записи)."""
    from identity_service.main import DEFINITION

    from gv_testkit import outbox_envelopes

    assert DEFINITION.tables is not None
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            from sqlalchemy.ext.asyncio import AsyncSession

            async with AsyncSession(bind=conn) as session:
                envelopes = await outbox_envelopes(session, DEFINITION.tables.outbox)
    finally:
        await engine.dispose()
    return [e.payload for e in envelopes if e.event_type == "identity.UserRolesChanged"]


async def test_reannounce_staff_perms_republishes_staff_only(seeding: Any) -> None:
    """Бэкфилл проекций: права сотрудника публикуются заново с большей версией (ADR 0058)."""
    from identity_service.wiring import reannounce_staff_perms

    settings, _ = seeding.settings(
        bootstrap_admin={"login": LOGIN, "password": SecretStr(PASSWORD)},
        reannounce_staff_perms=True,
    )
    await ensure_bootstrap_admin(settings)
    before = await _roles_events(seeding.database_url)
    assert len(before) == 1  # выдача роли admin при создании

    assert await reannounce_staff_perms(settings) == 1
    after = await _roles_events(seeding.database_url)
    assert len(after) == 2
    first, second = after
    assert second["user_id"] == first["user_id"]
    assert second["version"] > first["version"]
    assert second["perms"] == first["perms"]
    assert "admin" in second["roles"]
    assert second["reason"] == "staff_perms_backfill"
    assert second["actor_id"] is None
