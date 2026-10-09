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
import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker

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


@cache
def _spec(service: str) -> dict[str, Any]:
    from gv_testkit.contracts import contracts_dir

    path = contracts_dir() / "openapi" / f"{service}.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _template_matches(template: str, path: str) -> bool:
    pattern = "^" + re.sub(r"\\\{[^/]+?\\\}", "[^/]+", re.escape(template)) + "$"
    return re.match(pattern, path) is not None


_JSON_TYPES = ("application/json", "application/problem+json")
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_FORMATS = FormatChecker()


def _operation(service: str, method: str, path: str) -> tuple[dict[str, Any], str]:
    """Операция OpenAPI-снимка по методу и фактическому пути (и её шаблон пути)."""
    for template, item in _spec(service).get("paths", {}).items():
        op = item.get(method.lower())
        if op is not None and _template_matches(template, path):
            return op, template
    raise AssertionError(f"{method} {path} нет в contracts/openapi/{service}.json")


def _with_components(service: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {**schema, "components": _spec(service).get("components", {})}


def _json_schema(content: dict[str, Any] | None) -> dict[str, Any] | None:
    for media_type in _JSON_TYPES:
        schema = (content or {}).get(media_type, {}).get("schema")
        if schema is not None:
            return dict(schema)
    return None


def _assert_valid(where: str, schema: dict[str, Any], value: Any) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=_FORMATS).iter_errors(value), key=str
    )
    assert not errors, f"{where}: " + "; ".join(
        f"{list(e.absolute_path)}: {e.message}" for e in errors[:5]
    )


def _check_response(service: str, method: str, path: str, status: int, body: Any) -> None:
    """Тело ответа заглушки — по схеме контракта для этого статуса.

    Статус, которого нет в контракте (503 от недоступного сервиса, сеть), не проверяется:
    такой ответ формирует не сервис, а инфраструктура.
    """
    op, _ = _operation(service, method, path)
    response = op.get("responses", {}).get(str(status))
    if response is None:
        assert not 200 <= status < 300, f"{service} {method} {path}: {status} нет в контракте"
        return
    schema = _json_schema(response.get("content"))
    if schema is None:
        assert body is None, f"{service} {method} {path} {status}: ответ без тела"
        return
    assert body is not None, f"{service} {method} {path} {status}: нужно тело ответа"
    _assert_valid(f"{service} {method} {path} {status}", _with_components(service, schema), body)


def _check_request(service: str, request: httpx.Request) -> None:
    """Запрос админки к сервису — по контракту: тело JSON по схеме, мутация с Idempotency-Key.

    ``Idempotency-Key`` в снимке необязателен (заголовок читает зависимость
    ``gv_service.IdempotencyKey``, а не сигнатура ручки), но сервис без него отвечает 422 —
    поэтому для admin-мутаций проверяется здесь.
    """
    method, path = request.method, request.url.path
    op, template = _operation(service, method, path)
    where = f"{service} {method} {path} (запрос)"
    if method in _MUTATING and template.startswith("/admin/"):
        assert request.headers.get("Idempotency-Key"), f"{where}: нет Idempotency-Key"
    content = op.get("requestBody", {}).get("content")
    schema = _json_schema(content)
    if schema is not None:
        _assert_valid(where, _with_components(service, schema), json.loads(request.content))
    elif content and "multipart/form-data" in content:
        assert request.headers["content-type"].startswith("multipart/form-data"), where
        ref = content["multipart/form-data"]["schema"]["$ref"].rsplit("/", 1)[-1]
        form = _spec(service)["components"]["schemas"][ref]
        body = request.read()
        missing = [f for f in form.get("required", []) if f'name="{f}"'.encode() not in body]
        assert not missing, f"{where}: нет полей формы {missing}"


class ContractRoute:
    """Маршрут заглушки с проверкой по контракту: и ответа заглушки, и запроса админки."""

    def __init__(self, route: Any, service: str, method: str, path: str) -> None:
        self._route = route
        self._where = (service, method, path)

    def _reply(self, status_code: int, json_body: Any, kwargs: dict[str, Any]) -> ContractRoute:
        service = self._where[0]

        def handler(request: httpx.Request) -> httpx.Response:
            _check_request(service, request)
            if json_body is None:
                return httpx.Response(status_code, **kwargs)
            return httpx.Response(status_code, json=json_body, **kwargs)

        self._route.mock(side_effect=handler)
        return self

    def respond(self, status_code: int = 200, *, json: Any = None, **kwargs: Any) -> ContractRoute:
        """Ответ заглушки; тело обязано соответствовать контракту для этого статуса."""
        _check_response(*self._where, status_code, json)
        return self._reply(status_code, json, kwargs)

    def respond_off_contract(self, status_code: int, *, json: Any) -> ContractRoute:
        """Намеренно некорректный ответ — для тестов устойчивости к битым данным.

        Запрос админки по-прежнему проверяется по контракту.
        """
        return self._reply(status_code, json, {})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._route, name)


class ContractStub:
    """``gv_testkit.stub_service(service=...)`` + проверка тел ответов по контракту."""

    def __init__(self, stub: Any) -> None:
        self._stub = stub

    def route(self, method: str, path: str) -> ContractRoute:
        route = self._stub.route(method, path)
        return ContractRoute(route, self._stub.service, method.upper(), path)


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
    identity_version: int = 1,
) -> dict[str, Any]:
    return {
        "access_token": token,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "user": {"id": str(user_id), "display_name": "Модератор", "roles": [], "perms": []},
        "identity_version": identity_version,
    }


def _problem(status: int, code: str, title: str = "Ошибка") -> dict[str, Any]:
    return {"type": "about:blank", "title": title, "status": status, "code": code}


@pytest.fixture
def problem() -> Any:
    """Фабрика тела RFC 9457 (схема ``Problem`` контрактов): ``problem(401, "auth.x")``."""
    return _problem


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
