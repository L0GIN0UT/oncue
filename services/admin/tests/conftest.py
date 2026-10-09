"""Фикстуры тестов admin (Django + pytest-django). Инфраструктура и заглушки — из gv_testkit.

Тестовая БД — отдельная, с уникальным именем (``test_admin_<хвост uuid7>``) на общем
Postgres из ``GV_TEST_POSTGRES_URL``: параллельные прогоны в других рабочих копиях не мешают.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import django
import pytest

# До импорта Django: pytest-django и django.setup() читают модуль настроек из окружения.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "admin_service.django_settings")
# Как в плагине gv_testkit (он отрабатывает позже начальных conftest): прод-умолчание
# потребовало бы секрет Django.
os.environ.setdefault("GV_ENVIRONMENT", "ci")
# pytest-django настраивает Django при старте, только если модуль настроек известен заранее;
# в общем прогоне (`gv test` по всему монорепо) этот conftest загружается позже — поэтому
# инициализируем сами (идемпотентно), а фикстуры pytest-django подхватят настройки.
django.setup()

IDENTITY = "http://identity.test:8000"
CATALOG = "http://catalog.test:8000"
MEDIA = "http://media.test:8000"
JWKS = f"{IDENTITY}/.well-known/jwks.json"
ADMIN_PERMS = (
    "admin.access",
    "users.view",
    "roles.assign",
    "roles.manage",
    "catalog.import",
    "catalog.block_item",
    "catalog.edit_aliases",
    "media.upload_source",
    "media.regenerate",
)
MODERATOR_PERMS = ("admin.access", "users.view", "catalog.block_item", "catalog.edit_aliases")


@pytest.fixture(scope="session")
def django_db_modify_db_settings(infra: Any) -> None:
    """Подставить тестовый Postgres (пропуск теста локально, падение в CI, если его нет)."""
    from django.conf import settings
    from django.db import connections

    from gv_core.ids import uuid7

    parts = urlsplit(infra.postgres_url())
    # Словарь мутируется на месте: ConnectionHandler уже дополнил его значениями по умолчанию.
    db = settings.DATABASES["default"]
    db.update(
        {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": parts.path.lstrip("/") or "postgres",
            "USER": parts.username or "",
            "PASSWORD": parts.password or "",
            "HOST": parts.hostname or "",
            "PORT": str(parts.port or ""),
        }
    )
    db.setdefault("TEST", {})["NAME"] = f"test_admin_{uuid7().hex[-12:]}"
    # Обёртка соединения могла быть создана с заглушкой (без GV_DATABASE_URL) — пересоздать.
    del connections["default"]


@pytest.fixture(autouse=True)
def gv_settings(settings: Any) -> Any:
    """Адреса заглушек сервисов и издатель тестовых JWT."""
    from gv_testkit.auth import TEST_AUDIENCE, TEST_ISSUER

    settings.GV_IDENTITY_URL = IDENTITY
    settings.GV_JWKS_URL = JWKS
    settings.GV_JWT_ISSUER = TEST_ISSUER
    settings.GV_JWT_AUDIENCE = TEST_AUDIENCE
    settings.GV_SERVICE_URLS = {"identity": IDENTITY, "catalog": CATALOG, "media_pipeline": MEDIA}
    settings.GV_IP_ALLOWLIST = ["127.0.0.0/8"]
    return settings


@pytest.fixture
def identity(token_issuer: Any) -> Iterator[Any]:
    """Заглушка identity с JWKS тестового издателя.

    TODO(stage1): включить проверку контракта — ``service="identity"`` — после слияния
    потока A (contracts/openapi/identity.json пока заглушка).
    """
    from gv_testkit import stub_service

    with stub_service(IDENTITY) as stub:
        stub.route("GET", "/.well-known/jwks.json").respond(200, json=token_issuer.jwks())
        yield stub


@pytest.fixture
def catalog() -> Iterator[Any]:
    """Заглушка catalog. TODO(stage1): ``service="catalog"`` после слияния потока B."""
    from gv_testkit import stub_service

    with stub_service(CATALOG) as stub:
        yield stub


@pytest.fixture
def media() -> Iterator[Any]:
    """Заглушка media-pipeline. TODO(stage1): ``service="media_pipeline"`` после потока C."""
    from gv_testkit import stub_service

    with stub_service(MEDIA) as stub:
        yield stub


def _session_body(
    token: str, *, user_id: UUID, refresh: str = "r1", expires_in: int = 900
) -> dict[str, Any]:
    return {
        "access_token": token,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "user": {"id": str(user_id), "display_name": "Модератор", "roles": [], "perms": []},
    }


@pytest.fixture
def session_body() -> Any:
    """Фабрика ответа ``POST /internal/v1/admin-sessions`` (docs/plans/stage-1.md §2.5)."""
    return _session_body


@pytest.fixture
def login(client: Any, identity: Any, token_issuer: Any) -> Any:
    """Войти через форму админки с заданными ролями и правами; возвращает id пользователя."""
    from gv_core.ids import uuid7

    def do(perms: tuple[str, ...] = ADMIN_PERMS, roles: tuple[str, ...] = ("admin",)) -> UUID:
        user_id = uuid7()
        token = token_issuer.issue(user_id=user_id, roles=roles, perms=perms)
        identity.route("POST", "/internal/v1/admin-sessions").respond(
            200, json=_session_body(token, user_id=user_id)
        )
        response = client.post("/login/", {"username": "mod", "password": "secret-password"})
        assert response.status_code == 302, response.content.decode()[:500]
        return user_id

    return do
