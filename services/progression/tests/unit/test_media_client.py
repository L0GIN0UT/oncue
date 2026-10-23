"""Клиент media-pipeline для UGC: заголовки, multipart, маппинг ответов (ADR 0048)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import pytest
from progression_service.domain.avatar import (
    AvatarInvalidImageError,
    AvatarTooLargeError,
    AvatarUnavailableError,
    Moderation,
    RequestStatus,
)
from progression_service.infrastructure.media_client import UGC_PATH, HttpMediaUgcClient

from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

OWNER = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000b0001")
ASSET = "0192a3b4-c5d6-7e8f-9a0b-0000000c0201"
ACCEPTED = {"asset_id": ASSET, "submitted_at": "2026-10-23T12:00:00Z", "status": "pending"}


class Tokens:
    async def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer service-token"}


def client(handler: Any, *, tokens: Any = None) -> HttpMediaUgcClient:
    return HttpMediaUgcClient(
        base_url="http://media-pipeline:8000",
        timeout_s=1,
        tokens=lambda: tokens or Tokens(),
        max_bytes=1000,
        transport=httpx.MockTransport(handler),
    )


async def submit(c: HttpMediaUgcClient) -> Any:
    return await c.submit(
        owner_user_id=OWNER, purpose="avatar", data=b"\x89PNG-bytes", idempotency_key="idem-0001"
    )


async def test_sends_multipart_with_service_token_and_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json=ACCEPTED)

    result = await submit(client(handler))

    [request] = seen
    assert request.method == "POST" and request.url.path == UGC_PATH
    assert request.headers["Authorization"] == "Bearer service-token"
    assert request.headers["Idempotency-Key"] == "idem-0001"
    body = request.content
    assert str(OWNER).encode() in body and b'name="purpose"' in body and b"avatar" in body
    assert b"\x89PNG-bytes" in body
    assert b'filename="image"' in body  # имя файла клиента наружу не передаётся
    assert (str(result.asset_id), result.submitted_at.isoformat()) == (
        ASSET,
        "2026-10-23T12:00:00+00:00",
    )


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(413, json={"code": "ugc.too_large"}), AvatarTooLargeError),
        (httpx.Response(422, json={"code": "ugc.invalid_image"}), AvatarInvalidImageError),
        # чужая ошибка валидации - наша сторона (формат запроса), не вина игрока
        (httpx.Response(422, json={"code": "ugc.purpose_unknown"}), AvatarUnavailableError),
        (httpx.Response(403, json={"code": "ugc.client_forbidden"}), AvatarUnavailableError),
        (httpx.Response(401, json={"code": "auth.unauthenticated"}), AvatarUnavailableError),
        (httpx.Response(409, json={"code": "idempotency.key_reused"}), AvatarUnavailableError),
        (httpx.Response(500, text="boom"), AvatarUnavailableError),
        (httpx.Response(503, json={"code": "dependency.unavailable"}), AvatarUnavailableError),
        (httpx.Response(202, text="not json"), AvatarUnavailableError),
        (httpx.Response(202, json={"asset_id": "nope"}), AvatarUnavailableError),
        (httpx.Response(200, json=ACCEPTED), AvatarUnavailableError),
    ],
)
async def test_response_mapping(response: httpx.Response, error: type[Exception]) -> None:
    with pytest.raises(error):
        await submit(client(lambda _: response))


async def test_network_failure_and_token_failure_are_503_without_details() -> None:
    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("медленно")

    with pytest.raises(AvatarUnavailableError) as exc:
        await submit(client(boom))
    assert "медленно" not in exc.value.message

    class BrokenTokens:
        async def headers(self) -> dict[str, str]:
            raise RuntimeError("identity недоступен")

    with pytest.raises(AvatarUnavailableError):
        await submit(client(lambda _: httpx.Response(202, json=ACCEPTED), tokens=BrokenTokens()))


def test_too_large_error_reports_configured_limit() -> None:
    assert AvatarTooLargeError(123).details == {"max_bytes": 123}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": RequestStatus.PENDING, "public_key": None},
        {"status": RequestStatus.APPROVED, "public_key": None},
        {"status": RequestStatus.REJECTED, "public_key": "u/" + "a" * 32 + ".webp"},
        {"status": RequestStatus.APPROVED, "public_key": "u/../x.webp"},
        {"status": RequestStatus.REJECTED, "public_key": None, "version": 0},
    ],
)
def test_moderation_rejects_contradictory_decisions(kwargs: dict[str, Any]) -> None:
    from datetime import UTC, datetime

    base: dict[str, Any] = {
        "asset_id": uuid7(),
        "owner_user_id": uuid7(),
        "reason_code": None,
        "decided_at": datetime(2026, 10, 23, tzinfo=UTC),
        "version": 1,
    }
    with pytest.raises(ValueError):
        Moderation(**{**base, **kwargs})
