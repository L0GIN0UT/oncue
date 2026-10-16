"""Каркас сервиса: ошибки, health, correlation-id, метрики, авторизация, запуск процессов."""

from __future__ import annotations

import asyncio
import socket
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
import pytest
from fastapi import APIRouter, Depends
from pydantic import BaseModel, SecretStr

from gv_auth import Principal, require_perm
from gv_core import (
    ConflictError,
    DependencyUnavailableError,
    GoneError,
    NotFoundError,
    RateLimitedError,
)
from gv_core.context import current_context
from gv_observability.metrics import render_metrics
from gv_service import Infrastructure, ServiceDefinition, ServiceSettings, create_app
from gv_service.metrics_server import MetricsServer
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
        if item_id == "gone":
            raise GoneError("ассет выключен", code="asset.blocked")
        ctx = current_context()
        return {"id": item_id, "user": str(ctx.user_id) if ctx and ctx.user_id else ""}

    @router.get("/limited")
    async def limited(retry_after: str | None = None) -> None:
        # Числа — как int, остальное — строкой: обработчик должен пережить и мусор.
        details: dict[str, object] = {}
        if retry_after is not None:
            numeric = retry_after.lstrip("-").isdigit()
            details["retry_after"] = int(retry_after) if numeric else retry_after
        raise RateLimitedError("слишком часто", details=details)

    @router.get("/unavailable")
    async def unavailable() -> None:
        raise DependencyUnavailableError("tmdb недоступен", details={"retry_after": 30})

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
    kw.setdefault("metrics_port", None)  # порт 9100 общий для всех рабочих копий — не занимаем
    return ServiceSettings(service_name="demo", **kw)  # type: ignore[arg-type]


def _free_port() -> int:
    """Свободный порт: тесты параллельных рабочих копий не конфликтуют за фиксированный."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_ok(client: httpx.AsyncClient, url: str) -> None:
    async with asyncio.timeout(10):
        while True:
            try:
                if (await client.get(url)).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.05)


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

    async def test_gone_maps_to_410_with_code(self, client: httpx.AsyncClient) -> None:
        assert_problem(await client.get("/v1/items/gone"), 410, "asset.blocked")

    async def test_gone_default_code(self) -> None:
        assert GoneError("нет").code == "resource.gone"

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("", "1"),
            ("?retry_after=120", "120"),
            ("?retry_after=0", "1"),
            ("?retry_after=-5", "1"),
            ("?retry_after=soon", "1"),
        ],
    )
    async def test_retry_after_from_details(
        self, client: httpx.AsyncClient, query: str, expected: str
    ) -> None:
        response = await client.get(f"/v1/limited{query}")
        assert_problem(response, 429, "rate.limited")
        assert response.headers["retry-after"] == expected

    async def test_retry_after_on_503(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/v1/unavailable")
        assert_problem(response, 503, "dependency.unavailable")
        assert response.headers["retry-after"] == "30"

    async def test_no_retry_after_on_other_statuses(self, client: httpx.AsyncClient) -> None:
        assert "retry-after" not in (await client.get("/v1/items/conflict")).headers


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
        body, _ = render_metrics()
        text = body.decode()
        assert 'route="/v1/items/{item_id}"' in text
        assert "abc-123" not in text

    async def test_metrics_not_served_on_main_port(self, client: httpx.AsyncClient) -> None:
        """Основной порт смотрит в gateway: метрики там не отдаются (только на metrics_port)."""
        assert_problem(await client.get("/metrics"), 404, "route.not_found")

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
        port, metrics_port = _free_port(), _free_port()
        settings = _settings(http_port=port, metrics_port=metrics_port)
        runtime = Runtime(DEFINITION, Command.API, settings)
        task = asyncio.create_task(runtime.run())
        async with httpx.AsyncClient() as c:
            await _wait_ok(c, f"http://127.0.0.1:{port}/health/live")
            await _wait_ok(c, f"http://127.0.0.1:{metrics_port}/metrics")
            # /metrics — только на внутреннем порту, на основном его нет.
            assert (await c.get(f"http://127.0.0.1:{port}/metrics")).status_code == 404
            metrics = await c.get(f"http://127.0.0.1:{metrics_port}/metrics")
            assert "gv_http_requests_total" in metrics.text
            assert (await c.get(f"http://127.0.0.1:{metrics_port}/health/live")).status_code == 404
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

    @pytest.mark.parametrize(
        "command", [Command.API, Command.RELAY, Command.CONSUMER, Command.WORKER, Command.ALL]
    )
    def test_every_command_gets_metrics_server(
        self, command: Command, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Метрики есть у всех процессов, включая consumer, worker и relay без HTTP API."""
        runtime = Runtime(DEFINITION, command, _settings(metrics_port=9100))

        async def idle() -> None:
            return None

        for name in ("_relay_factory", "_consumer_factory", "_worker_factory"):
            monkeypatch.setattr(runtime, name, lambda _infra: idle)
        infra = Infrastructure(settings=runtime.settings, tables=None)
        assert "metrics" in runtime._components(infra, tracing=False)

    async def test_worker_consumes_with_configured_prefetch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Регресс засева 3a: число одновременных задач задаётся настройкой, а не константой 10."""
        seen: list[tuple[str, int]] = []

        class FakeQueue:
            def __init__(self, url: str, service: str) -> None: ...
            async def start(self) -> None: ...
            async def stop(self) -> None: ...
            async def consume(
                self, task_type: str, handler: object, stop: object, prefetch: int
            ) -> None:
                seen.append((task_type, prefetch))

        async def handler(payload: dict[str, object]) -> None: ...

        monkeypatch.setattr("gv_service.runner.RabbitTaskQueue", FakeQueue)
        definition = ServiceDefinition(
            name="demo",
            settings_cls=ServiceSettings,
            build_task_handlers=lambda _infra: {"demo.work": handler},
        )
        settings = _settings(rabbitmq_url=SecretStr("amqp://x"), task_prefetch=2)
        runtime = Runtime(definition, Command.WORKER, settings)
        run = runtime._worker_factory(Infrastructure(settings=settings, tables=None))
        assert run is not None
        await run()
        assert seen == [("demo.work", 2)]
        assert ServiceSettings(service_name="demo").task_prefetch == 10

    def test_metrics_port_none_disables_metrics_server(self) -> None:
        runtime = Runtime(DEFINITION, Command.API, _settings(metrics_port=None))
        infra = Infrastructure(settings=runtime.settings, tables=None)
        assert "metrics" not in runtime._components(infra, tracing=False)


@pytest.mark.component
class TestMetricsServer:
    async def test_serves_only_metrics_and_stops(self) -> None:
        server = MetricsServer("127.0.0.1", 0)
        task = asyncio.create_task(server.serve())
        for _ in range(1000):
            if server.started:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("сервер метрик не поднялся")
        base = f"http://127.0.0.1:{server.bound_port}"
        async with httpx.AsyncClient() as c:
            response = await c.get(f"{base}/metrics")
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/plain")
            assert (await c.head(f"{base}/metrics")).content == b""
            assert (await c.get(f"{base}/v1/items/1")).status_code == 404
            assert (await c.post(f"{base}/metrics")).status_code == 404
        server.stop()
        await asyncio.wait_for(task, timeout=10)

    async def test_busy_port_fails_loudly(self) -> None:
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            server = MetricsServer("127.0.0.1", int(busy.getsockname()[1]))
            with pytest.raises(RuntimeError, match="метрик"):
                await asyncio.wait_for(server.serve(), timeout=10)


def test_environment_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Забытая GV_ENVIRONMENT не должна включать /docs и отладочное поведение."""
    monkeypatch.delenv("GV_ENVIRONMENT", raising=False)
    assert ServiceSettings(service_name="demo").is_production
