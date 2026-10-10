"""ServiceClient: заголовки, Idempotency-Key, Problem Details → сообщение."""

from __future__ import annotations

import httpx
import pytest
from admin_service.core.service_client import ServiceClient, ServiceError

pytestmark = pytest.mark.unit


def _client(handler: httpx.MockTransport) -> ServiceClient:
    return ServiceClient(
        "catalog",
        base_url="http://catalog",
        access_token="tok",
        request_id="rid",
        transport=handler,
    )


def test_headers_and_json() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with _client(httpx.MockTransport(handle)) as client:
        assert client.post("/x", idempotency_key="k1", json={"a": 1}) == {"ok": True}
        assert client.get("/y", params={"q": "", "n": None, "d": "film"}) == {"ok": True}
    assert seen[0].headers["Authorization"] == "Bearer tok"
    assert seen[0].headers["X-Request-Id"] == "rid"
    assert seen[0].headers["Idempotency-Key"] == "k1"
    assert "Idempotency-Key" not in seen[1].headers
    assert dict(seen[1].url.params) == {"d": "film"}


def test_mutation_without_key_is_programming_error() -> None:
    with (
        _client(httpx.MockTransport(lambda _: httpx.Response(200))) as client,
        pytest.raises(ValueError),
    ):
        client.put("/x", idempotency_key="", json={})


def test_no_content() -> None:
    with _client(httpx.MockTransport(lambda _: httpx.Response(204))) as client:
        assert client.post("/x", idempotency_key="k", json={}) is None


@pytest.mark.parametrize(
    ("response", "code", "message"),
    [
        (
            httpx.Response(409, json={"status": 409, "code": "resource.conflict", "title": "C"}),
            "resource.conflict",
            "Данные изменились — обновите страницу и повторите",
        ),
        (
            httpx.Response(410, json={"code": "asset.blocked", "detail": "единица заблокирована"}),
            "asset.blocked",
            "единица заблокирована",
        ),
        (
            httpx.Response(
                422,
                json={
                    "code": "request.validation_failed",
                    "errors": [{"loc": ["body", "reason"], "msg": "field required"}],
                },
            ),
            "request.validation_failed",
            "Сервис отклонил данные формы: body.reason: field required",
        ),
        (
            httpx.Response(502, text="<html>bad gateway</html>"),
            "http.502",
            "Ошибка сервиса catalog",
        ),
    ],
)
def test_problem_details_mapping(response: httpx.Response, code: str, message: str) -> None:
    with (
        _client(httpx.MockTransport(lambda _: response)) as client,
        pytest.raises(ServiceError) as err,
    ):
        client.get("/x")
    assert (err.value.status, err.value.code, err.value.message) == (
        response.status_code,
        code,
        message,
    )


def test_transport_error_is_unavailable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("t", request=request)

    with _client(httpx.MockTransport(boom)) as client, pytest.raises(ServiceError) as err:
        client.get("/x")
    assert (err.value.status, err.value.code) == (None, "service.unavailable")


def test_non_json_success_is_bad_response() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, text="ok"))
    with _client(transport) as client, pytest.raises(ServiceError, match="bad_response"):
        client.get("/x")


def test_query_is_post_without_idempotency_key() -> None:
    """Проверка конфига (validate) — POST без побочных эффектов: ключ не нужен и не шлётся."""
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    with _client(httpx.MockTransport(handle)) as client:
        assert client.query("/v", json={"body": {}}) == {"ok": True}
    assert seen[0].method == "POST"
    assert "Idempotency-Key" not in seen[0].headers


def test_game_error_codes_have_human_messages() -> None:
    response = httpx.Response(409, json={"status": 409, "code": "game.daily_locked", "title": "L"})
    with (
        _client(httpx.MockTransport(lambda _: response)) as client,
        pytest.raises(ServiceError) as exc,
    ):
        client.put("/x", idempotency_key="k", json={})
    assert exc.value.code == "game.daily_locked"
    assert "заблокирован" in exc.value.message
