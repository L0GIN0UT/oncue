"""Кирпичи admin-мутаций: заголовок ``Idempotency-Key``, поле ``reason``, ошибки в OpenAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from gv_service import (
    IdempotencyKey,
    Infrastructure,
    Problem,
    Reason,
    ServiceDefinition,
    ServiceSettings,
    create_app,
    problem_responses,
)
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component


class BlockBody(BaseModel):
    reason: Reason


def build_routers(_: Infrastructure) -> list[APIRouter]:
    router = APIRouter(prefix="/admin/v1")

    @router.post("/things/{thing_id}/block", responses=problem_responses(404, 409, 422))
    async def block(thing_id: str, body: BlockBody, key: IdempotencyKey) -> dict[str, str]:
        return {"id": thing_id, "key": key, "reason": body.reason}

    return [router]


DEFINITION = ServiceDefinition(
    name="demo", settings_cls=ServiceSettings, build_routers=build_routers
)


@pytest.fixture
async def app_and_client(
    token_issuer: TestTokenIssuer,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    settings = ServiceSettings(service_name="demo", metrics_port=None)
    infra = await Infrastructure.open(settings, tables=None)
    app = create_app(DEFINITION, infra, jwt_verifier=token_issuer.verifier())
    async with service_client(app) as client:
        yield app, client
    await infra.close()


@pytest.fixture
def client(app_and_client: tuple[Any, httpx.AsyncClient]) -> httpx.AsyncClient:
    return app_and_client[1]


def _post(client: httpx.AsyncClient, headers: dict[str, str], reason: str = "спам") -> Any:
    return client.post("/admin/v1/things/t1/block", json={"reason": reason}, headers=headers)


class TestIdempotencyKey:
    @pytest.mark.parametrize(
        "key", ["abcdefgh", "a" * 128, "Key_1.2:3-x", "01920000-0000-7000-8000-000000000001"]
    )
    async def test_valid_key_is_passed_to_handler(
        self, client: httpx.AsyncClient, key: str
    ) -> None:
        response = await _post(client, {"Idempotency-Key": key})
        assert response.status_code == 200, response.text
        assert response.json()["key"] == key

    async def test_missing_key_is_422_key_required(self, client: httpx.AsyncClient) -> None:
        body = assert_problem(await _post(client, {}), 422, "idempotency.key_required")
        assert "Idempotency-Key" in body["title"]

    @pytest.mark.parametrize(
        "key",
        ["", "short", "a" * 129, "has space1", "a/b/c/d/e", "ключ-ключ-1".encode()],
    )
    async def test_malformed_key_is_422_key_invalid(
        self, client: httpx.AsyncClient, key: str | bytes
    ) -> None:
        response = await client.post(
            "/admin/v1/things/t1/block",
            json={"reason": "спам"},
            # bytes — чтобы не-ASCII ушёл в заголовок как есть (UTF-8).
            headers=[(b"Idempotency-Key", key if isinstance(key, bytes) else key.encode())],
        )
        assert_problem(response, 422, "idempotency.key_invalid")


class TestReason:
    async def test_reason_is_stripped(self, client: httpx.AsyncClient) -> None:
        response = await _post(client, {"Idempotency-Key": "k-reason-01"}, reason="  дубль  ")
        assert response.json()["reason"] == "дубль"

    @pytest.mark.parametrize("reason", ["", "  ab  ", "x" * 501])
    async def test_reason_length_is_checked_after_strip(
        self, client: httpx.AsyncClient, reason: str
    ) -> None:
        body = assert_problem(
            await _post(client, {"Idempotency-Key": "k-reason-02"}, reason=reason),
            422,
            "request.validation_failed",
        )
        assert body["errors"][0]["loc"] == ["body", "reason"]

    def test_bounds_inclusive(self) -> None:
        assert BlockBody(reason="abc").reason == "abc"
        assert BlockBody(reason="x" * 500).reason == "x" * 500
        with pytest.raises(ValidationError):
            BlockBody(reason="x" * 501)


class TestOpenApi:
    def test_problem_responses_describe_errors(
        self, app_and_client: tuple[Any, httpx.AsyncClient]
    ) -> None:
        schema = app_and_client[0].openapi()
        op = schema["paths"]["/admin/v1/things/{thing_id}/block"]["post"]
        for status in ("404", "409", "422"):
            content = op["responses"][status]["content"]
            assert content["application/problem+json"]["schema"] == {
                "$ref": "#/components/schemas/Problem"
            }
        problem = schema["components"]["schemas"]["Problem"]
        assert {"type", "title", "status", "code"} <= set(problem["required"])
        assert {"details", "errors", "correlation_id"} <= set(problem["properties"])

    def test_idempotency_header_documented(
        self, app_and_client: tuple[Any, httpx.AsyncClient]
    ) -> None:
        op = app_and_client[0].openapi()["paths"]["/admin/v1/things/{thing_id}/block"]["post"]
        header = next(p for p in op["parameters"] if p["name"] == "Idempotency-Key")
        assert header["in"] == "header"
        assert "8–128" in header["description"]

    def test_problem_model_accepts_handler_output(self) -> None:
        payload = {
            "type": "urn:guessverse:problem:x.y",
            "title": "t",
            "status": 409,
            "code": "x.y",
            "details": {"a": 1},
        }
        assert Problem.model_validate(payload).details == {"a": 1}
