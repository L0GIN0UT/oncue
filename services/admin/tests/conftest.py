"""Фикстуры тестов admin (Django + pytest-django). Инфраструктура и заглушки — из gv_testkit.

Тестовая БД — отдельная, с уникальным именем (``test_admin_<хвост uuid7>``) на общем
Postgres из ``GV_TEST_POSTGRES_URL``: параллельные прогоны в других рабочих копиях не мешают.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from functools import cache
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import django
import pytest
from jsonschema import Draft202012Validator

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


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup: Any, django_db_blocker: Any) -> None:
    """Тестовая БД после миграций: выключить запрет TRUNCATE аудита (``audit/0002``).

    Транзакционные тесты (``transaction=True``) очищают БД через ``flush`` = ``TRUNCATE``
    всех таблиц; триггер это запрещает. Выключается только в тестовой БД; тест
    ``test_truncate_is_blocked_by_trigger`` включает его обратно внутри своей транзакции.
    """
    from django.db import connection

    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute("ALTER TABLE audit_auditentry DISABLE TRIGGER audit_auditentry_no_truncate")


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
    # Счётчики лимита входа (кэш в памяти процесса) не должны переходить между тестами.
    from django.core.cache import cache

    cache.clear()
    return settings


# Ручки, которые добавляются параллельными потоками этапа fix1 и ещё не попали в снимок
# contracts/openapi/ этой рабочей копии. Для них проверка «ручка есть в контракте»
# пропускается точечно; тела проверяются, если схема уже есть.
# TODO(fix1): убрать после слияния fix1/identity и `gv contracts export` (оркестратор):
#   identity POST /internal/v1/admin-sessions/revoke {refresh_token} → 204.
PENDING_OPERATIONS = frozenset({("identity", "POST", "/internal/v1/admin-sessions/revoke")})


@cache
def _spec(service: str) -> dict[str, Any]:
    from gv_testkit.contracts import contracts_dir

    path = contracts_dir() / "openapi" / f"{service}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _template_matches(template: str, path: str) -> bool:
    pattern = "^" + re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(template)) + "$"
    return re.match(pattern, path) is not None


def _response_schema(service: str, method: str, path: str, status: int) -> dict[str, Any] | None:
    """Схема JSON-ответа операции из OpenAPI-снимка (с ``components`` для ``$ref``)."""
    spec = _spec(service)
    for template, item in spec.get("paths", {}).items():
        op = item.get(method.lower())
        if op is None or not _template_matches(template, path):
            continue
        response = op.get("responses", {}).get(str(status), {})
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        if schema is None:
            return None
        return {**schema, "components": spec.get("components", {})}
    return None


class ContractRoute:
    """Маршрут заглушки, чьи успешные JSON-ответы проверяются по схеме контракта."""

    def __init__(self, route: Any, service: str, method: str, path: str) -> None:
        self._route = route
        self._where = (service, method, path)

    def respond(self, status_code: int = 200, *, json: Any = None, **kwargs: Any) -> ContractRoute:
        """Ответ заглушки; 2xx с JSON обязан соответствовать контракту сервиса."""
        if json is not None and 200 <= status_code < 300:
            schema = _response_schema(*self._where, status_code)
            assert schema is not None, f"{self._where} {status_code}: нет схемы ответа"
            errors = sorted(Draft202012Validator(schema).iter_errors(json), key=str)
            assert not errors, f"{self._where} {status_code}: " + "; ".join(
                f"{list(e.absolute_path)}: {e.message}" for e in errors[:5]
            )
        self._route.respond(status_code, json=json, **kwargs)
        return self

    def respond_off_contract(self, status_code: int, *, json: Any) -> ContractRoute:
        """Намеренно некорректный ответ — для тестов устойчивости к битым данным."""
        self._route.respond(status_code, json=json)
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._route, name)


class ContractStub:
    """``gv_testkit.stub_service(service=...)`` + проверка тел ответов по контракту."""

    def __init__(self, stub: Any) -> None:
        self._stub = stub

    def route(self, method: str, path: str) -> ContractRoute:
        service = self._stub.service
        if (service, method.upper(), path) in PENDING_OPERATIONS:
            self._stub.service = None  # точечный пропуск, см. PENDING_OPERATIONS
            try:
                route = self._stub.route(method, path)
            finally:
                self._stub.service = service
        else:
            route = self._stub.route(method, path)
        return ContractRoute(route, service, method.upper(), path)


def _contract_stub(base_url: str, service: str) -> Iterator[ContractStub]:
    from gv_testkit import stub_service

    with stub_service(base_url, service=service) as stub:
        yield ContractStub(stub)


@pytest.fixture
def identity(token_issuer: Any) -> Iterator[ContractStub]:
    """Заглушка identity (по contracts/openapi/identity.json) с JWKS тестового издателя."""
    for stub in _contract_stub(IDENTITY, "identity"):
        stub.route("GET", "/.well-known/jwks.json").respond(200, json=token_issuer.jwks())
        yield stub


@pytest.fixture
def catalog() -> Iterator[ContractStub]:
    """Заглушка catalog (по contracts/openapi/catalog.json)."""
    yield from _contract_stub(CATALOG, "catalog")


@pytest.fixture
def media() -> Iterator[ContractStub]:
    """Заглушка media-pipeline (по contracts/openapi/media_pipeline.json)."""
    yield from _contract_stub(MEDIA, "media_pipeline")


def _session_body(
    token: str,
    *,
    user_id: UUID,
    refresh: str = "r1",
    expires_in: int = 900,
    identity_version: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "access_token": token,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "user": {"id": str(user_id), "display_name": "Модератор", "roles": [], "perms": []},
    }
    # identity_version — поле из потока fix1/identity (в снимке контракта может ещё не быть;
    # дополнительные поля схема ответа допускает).
    if identity_version is not None:
        body["identity_version"] = identity_version
    return body


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
