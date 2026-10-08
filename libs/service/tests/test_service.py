"""Каркас сервиса: ошибки, health, correlation-id, метрики, авторизация, запуск процессов."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import APIRouter, Depends
from pydantic import BaseModel, SecretStr

from gv_auth import Principal, require_perm
from gv_core import ConflictError, NotFoundError
from gv_core.context import current_context
from gv_service import Infrastructure, ServiceDefinition, ServiceSettings, create_app
from gv_service.runner import Command, Runtime
from gv_testkit import TestTokenIssuer, assert_problem, service_client
from gv_testkit.infra import InfraProvider


class Body(BaseModel):
    n: int


def build_routers(_: Infrastructure) -> list[APIRouter]:
    router = APIRouter(prefix="/v1")

    @router.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        if item_id == "missing":
            raise NotFoundError("нет такого", code="item.not_found")
        if item_id == "conflict":
            raise ConflictError("версия устарела", details={"expected": 3})
        if item_id == "boom":
            raise RuntimeError("секретная внутренняя деталь")
        ctx = current_context()
        return {"id": item_id, "user": str(ctx.user_id) if ctx and ctx.user_id else ""}

    @router.post("/items")
    async def create(body: Body) -> Body:
        return body

    @router.get("/admin/secret")
    async def secret(
        p: Annotated[Principal, Depends(require_perm("admin.read"))],
    ) -> dict[str, str]:
        return {"user": str(p.user_id)}

    @router.get("/me")
    async def me(p: Annotated[Principal, Depends(require_perm("profile.read"))]) -> dict[str, str]:
        ctx = current_context()
        return {"ctx_user": str(ctx.user_id) if ctx else ""}

    return [router]


DEFINITION = ServiceDefinition(
    name="demo", settings_cls=ServiceSettings, build_routers=build_routers
)


def _settings(**kw: object) -> ServiceSettings:
    return ServiceSettings(service_name="demo", **kw)  # type: ignore[arg-type]


@pytest.fixture
async def service_infra() -> AsyncIterator[Infrastructure]:
    infra = await Infrastructure.open(_settings(), tables=None)
    yield infra
    await infra.close()


@pytest.fixture
async def client(
    service_infra: Infrastructure, token_issuer: TestTokenIssuer
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(DEFINITION, service_infra, jwt_verifier=token_issuer.verifier())
    async with service_client(app) as c:
        yield c


@pytest.mark.component
class TestProblems:
    async def test_not_found_maps_to_404_with_code(self, client: httpx.AsyncClient) -> None:
        body = assert_problem(await client.get("/v1/items/missing"), 404, "item.not_found")
        assert uuid.UUID(body["correlation_id"])

    async def test_conflict_with_details(self, client: httpx.AsyncClient) -> None:
        body = assert_problem(await client.get("/v1/items/conflict"), 409, "resource.conflict")
        assert body["details"] == {"expected": 3}

    async def test_unexpected_error_hides_details(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/items/boom")
        body = assert_problem(response, 500, "internal.error")
        assert "секретная" not in response.text
        assert "detail" not in body
        # 500 тоже связан с цепочкой запроса: по id находится лог с трейсбеком.
        assert response.headers["x-request-id"] == body["correlation_id"]

    async def test_validation_error(self, client: httpx.AsyncClient) -> None:
        body = assert_problem(
            await client.post("/v1/items", json={"n": "x"}), 422, "request.validation_failed"
        )
        assert body["errors"][0]["loc"] == ["body", "n"]

    async def test_unknown_route(self, client: httpx.AsyncClient) -> None:
        assert_problem(await client.get("/nope"), 404, "route.not_found")


@pytest.mark.component
class TestRequestId:
    async def test_valid_incoming_id_is_kept(self, client: httpx.AsyncClient) -> None:
        rid = str(uuid.uuid4())
        response = await client.get("/v1/items/missing", headers={"X-Request-Id": rid})
        assert response.headers["x-request-id"] == rid
        assert response.json()["correlation_id"] == rid

    async def test_garbage_incoming_id_is_replaced(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            "/v1/items/1", headers={"X-Request-Id": "<script>\nlog-injection"}
        )
        assert uuid.UUID(response.headers["x-request-id"]).version == 7


@pytest.mark.component
class TestAuth:
    async def test_missing_token_is_401(self, client: httpx.AsyncClient) -> None:
        assert_problem(await client.get("/v1/admin/secret"), 401, "auth.unauthenticated")

    async def test_insufficient_perms_is_403(
        self, client: httpx.AsyncClient, token_issuer: TestTokenIssuer
    ) -> None:
        token = token_issuer.issue(perms=["profile.read"])
        response = await client.get(
            "/v1/admin/secret", headers={"Authorization": f"Bearer {token}"}
        )
        assert_problem(response, 403, "auth.forbidden")

    async def test_valid_perm_and_user_in_context(
        self, client: httpx.AsyncClient, token_issuer: TestTokenIssuer
    ) -> None:
        user = uuid.uuid4()
        token = token_issuer.issue(user_id=user, perms=["profile.read", "admin.read"])
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/v1/admin/secret", headers=headers)).json() == {"user": str(user)}
        assert (await client.get("/v1/me", headers=headers)).json() == {"ctx_user": str(user)}

    async def test_cookie_token(
        self, client: httpx.AsyncClient, token_issuer: TestTokenIssuer
    ) -> None:
        token = token_issuer.issue(perms=["admin.read"])
        client.cookies.set("gv_access", token)
        assert (await client.get("/v1/admin/secret")).status_code == 200

    async def test_foreign_token_is_rejected(self, client: httpx.AsyncClient) -> None:
        other = TestTokenIssuer()  # тот же kid, другой ключ
        response = await client.get(
            "/v1/admin/secret",
            headers={"Authorization": f"Bearer {other.issue(perms=['admin.read'])}"},
        )
        assert_problem(response, 401, "auth.token_invalid")


@pytest.mark.component
class TestHealthAndMetrics:
    async def test_live_and_ready_without_dependencies(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/health/live")).json() == {"status": "ok"}
        ready = await client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ok", "checks": {}}

    async def test_metrics_use_route_template(self, client: httpx.AsyncClient) -> None:
        await client.get("/v1/items/abc-123")
        text = (await client.get("/metrics")).text
        assert 'route="/v1/items/{item_id}"' in text
        assert "abc-123" not in text

    async def test_ready_is_503_while_shutting_down(self, service_infra: Infrastructure) -> None:
        app = create_app(DEFINITION, service_infra, is_shutting_down=lambda: True)
        async with service_client(app) as c:
            assert (await c.get("/health/ready")).status_code == 503
            # live не зависит от shutdown: процесс жив и дообрабатывает запросы.
            assert (await c.get("/health/live")).status_code == 200


@pytest.mark.integration
class TestReadinessWithRealDependencies:
    async def test_ready_checks_postgres_and_redis(
        self, infra: InfraProvider, database_url: str
    ) -> None:
        settings = _settings(
            database_url=SecretStr(database_url), redis_url=SecretStr(infra.redis_url())
        )
        real = await Infrastructure.open(settings, tables=None)
        try:
            async with service_client(create_app(DEFINITION, real)) as c:
                response = await c.get("/health/ready")
            assert response.json() == {"status": "ok", "checks": {"postgres": True, "redis": True}}
        finally:
            await real.close()

    async def test_ready_fails_when_dependency_down(self) -> None:
        settings = _settings(redis_url=SecretStr("redis://127.0.0.1:1/0"))
        real = await Infrastructure.open(settings, tables=None)
        try:
            async with service_client(create_app(DEFINITION, real)) as c:
                response = await c.get("/health/ready")
            assert response.status_code == 503
            assert response.json()["checks"] == {"redis": False}
        finally:
            await real.close()


@pytest.mark.component
class TestRuntime:
    async def test_api_starts_and_stops_gracefully(self) -> None:
        settings = _settings(http_port=58123)
        runtime = Runtime(DEFINITION, Command.API, settings)
        task = asyncio.create_task(runtime.run())
        async with httpx.AsyncClient() as c:
            for _ in range(100):
                try:
                    if (await c.get("http://127.0.0.1:58123/health/live")).status_code == 200:
                        break
                except httpx.TransportError:
                    await asyncio.sleep(0.05)
            else:
                pytest.fail("api не поднялся")
        runtime.request_shutdown()
        await asyncio.wait_for(task, timeout=10)
        assert runtime.shutting_down

    def test_explicitly_requested_unconfigured_component_fails(self) -> None:
        runtime = Runtime(DEFINITION, Command.RELAY, _settings())
        infra = Infrastructure(settings=runtime.settings, tables=None)
        with pytest.raises(SystemExit, match="relay"):
            runtime._components(infra, tracing=False)

    def test_all_mode_skips_unconfigured_components(self) -> None:
        runtime = Runtime(DEFINITION, Command.ALL, _settings())
        infra = Infrastructure(settings=runtime.settings, tables=None)
        assert list(runtime._components(infra, tracing=False)) == ["api"]


def test_environment_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Забытая GV_ENVIRONMENT не должна включать /docs и отладочное поведение."""
    monkeypatch.delenv("GV_ENVIRONMENT", raising=False)
    assert ServiceSettings(service_name="demo").is_production
