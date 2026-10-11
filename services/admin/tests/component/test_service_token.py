"""Сервисный токен admin → identity ``/internal/v1/admin-sessions*`` (D13, §2.8 п. 4).

Источник токена подменяется фейком: провайдер с кэшем и обновлением — из ``gv_auth``
(шаг 0b) и тестируется там; здесь проверяется, что admin кладёт токен в каждый вызов
internal-ручек identity и правильно обрабатывает отказ его получения.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from admin_service import django_settings
from admin_service.core import service_auth
from admin_service.core.models import AdminUser
from admin_service.settings import Settings
from pydantic import SecretStr

from gv_auth import SyncServiceTokenProvider
from gv_core.errors import DependencyUnavailableError
from gv_core.ids import uuid7
from gv_service import ServiceAuthSettings

pytestmark = [pytest.mark.component, pytest.mark.django_db]


def _with_service_auth(auth: ServiceAuthSettings) -> Settings:
    """Настройки admin процесса с заданными ``GV_SERVICE_AUTH__*``."""
    return django_settings.GV.model_copy(update={"service_auth": auth})


class FakeTokenSource:
    """Источник токена: отдаёт заданный токен или отказывает, как лежащий identity."""

    def __init__(self, token: str | None) -> None:
        self.token = token
        self.calls = 0

    def headers(self) -> dict[str, str]:
        self.calls += 1
        if self.token is None:
            raise DependencyUnavailableError("identity недоступен")
        return {"Authorization": f"Bearer {self.token}"}


@pytest.fixture
def use_source(monkeypatch: pytest.MonkeyPatch) -> Callable[[str | None], FakeTokenSource]:
    def install(token: str | None) -> FakeTokenSource:
        source = FakeTokenSource(token)
        monkeypatch.setattr(service_auth, "token_source", lambda: source)
        return source

    return install


def test_login_and_logout_carry_service_token(  # noqa: PLR0917 — фикстуры pytest
    client: Any,
    identity: Any,
    token_issuer: Any,
    session_body: Any,
    service_token: Any,
    use_source: Any,
) -> None:
    svc = service_token("admin", "identity", ["admin_sessions"])
    use_source(svc)
    user_id = uuid7()
    access = token_issuer.issue(user_id=user_id, roles=["admin"], perms=["admin.access"])
    login = identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(access, user_id=user_id)
    )
    revoke = identity.route("POST", "/internal/v1/admin-sessions/revoke").respond(204)

    assert client.post("/login/", {"username": "mod", "password": "pw-123456"}).status_code == 302
    client.post("/logout/")

    assert login.calls.last.request.headers["Authorization"] == f"Bearer {svc}"
    assert revoke.calls.last.request.headers["Authorization"] == f"Bearer {svc}"


def test_service_token_failure_means_identity_unavailable(
    client: Any, identity: Any, use_source: Any
) -> None:
    use_source(None)
    login = identity.route("POST", "/internal/v1/admin-sessions").respond(503, json={"status": 503})
    response = client.post("/login/", {"username": "mod", "password": "pw-123456"})
    assert response.status_code == 200
    assert "Вход временно невозможен" in response.content.decode()
    assert not login.called  # пароль не ушёл в identity без сервисного токена
    assert not AdminUser.objects.exists()


def test_logout_succeeds_without_service_token(
    client: Any, login: Any, identity: Any, use_source: Any
) -> None:
    login()
    use_source(None)
    revoke = identity.route("POST", "/internal/v1/admin-sessions/revoke").respond(204)
    response = client.post("/logout/")
    assert response.status_code in (200, 302)
    assert not revoke.called


def test_without_configured_source_calls_go_without_service_token(
    client: Any,
    identity: Any,
    token_issuer: Any,
    session_body: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без ``GV_SERVICE_AUTH__*`` источник не собирается: вызовы без ``Authorization``."""
    monkeypatch.setattr(django_settings, "GV", _with_service_auth(ServiceAuthSettings()))
    monkeypatch.setattr(service_auth, "_state", {})
    assert service_auth.build_token_source() is None
    user_id = uuid7()
    access = token_issuer.issue(user_id=user_id, roles=["admin"], perms=["admin.access"])
    route = identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(access, user_id=user_id)
    )
    client.post("/login/", {"username": "mod", "password": "pw-123456"})
    assert "Authorization" not in route.calls.last.request.headers


def test_configured_source_is_sync_provider_for_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = ServiceAuthSettings(
        token_url="http://identity.test/internal/v1/service-tokens",
        client_id="admin",
        client_secret=SecretStr("admin-secret"),
    )
    monkeypatch.setattr(django_settings, "GV", _with_service_auth(auth))
    source = service_auth.build_token_source()
    assert isinstance(source, SyncServiceTokenProvider)
    assert source.audience == service_auth.IDENTITY_AUDIENCE == "identity"
    assert "admin-secret" not in repr(source)
    source.close()


def test_login_gets_service_token_from_identity(  # noqa: PLR0917 — фикстуры pytest
    client: Any,
    identity: Any,
    token_issuer: Any,
    session_body: Any,
    service_token: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сквозь настоящий провайдер: client credentials → токен → вызов admin-sessions."""
    auth = ServiceAuthSettings(
        token_url="http://identity.test:8000/internal/v1/service-tokens",
        client_id="admin",
        client_secret=SecretStr("admin-secret"),
    )
    monkeypatch.setattr(django_settings, "GV", _with_service_auth(auth))
    monkeypatch.setattr(service_auth, "_state", {})
    svc = service_token("admin", "identity", ["admin_sessions"])
    issue = identity.route("POST", "/internal/v1/service-tokens").respond(
        200, json={"access_token": svc, "token_type": "Bearer", "expires_in": 300}
    )
    user_id = uuid7()
    access = token_issuer.issue(user_id=user_id, roles=["admin"], perms=["admin.access"])
    login = identity.route("POST", "/internal/v1/admin-sessions").respond(
        200, json=session_body(access, user_id=user_id)
    )
    assert client.post("/login/", {"username": "mod", "password": "pw-123456"}).status_code == 302
    assert json.loads(issue.calls.last.request.content) == {
        "client_id": "admin",
        "client_secret": "admin-secret",
        "audience": "identity",
    }
    assert login.calls.last.request.headers["Authorization"] == f"Bearer {svc}"


def test_token_source_is_built_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[FakeTokenSource] = []

    def build() -> FakeTokenSource:
        built.append(FakeTokenSource("t"))
        return built[-1]

    monkeypatch.setattr(service_auth, "_state", {})
    monkeypatch.setattr(service_auth, "build_token_source", build)
    assert service_auth.token_source() is service_auth.token_source()
    assert len(built) == 1
