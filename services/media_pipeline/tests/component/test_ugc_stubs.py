"""Заглушки UGC-изображений 5b (шаг 0a, ADR 0048): доступ по контракту, до потока S3 - 501."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from media_pipeline_service.main import DEFINITION
from media_pipeline_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

ASSET = "0192a3b4-c5d6-7e8f-9a0b-0000000c0201"
OWNER = "0192a3b4-c5d6-7e8f-9a0b-0000000b0001"
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
REASON = {"reason": "проверка контракта"}
PERM = "media.ugc.moderate"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
ADMIN_STUBS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/admin/v1/ugc-images/settings", {}),
    ("GET", "/admin/v1/ugc-images", {}),
    ("GET", f"/admin/v1/ugc-images/{ASSET}/preview", {}),
    ("POST", f"/admin/v1/ugc-images/{ASSET}/approve", {"json": REASON}),
    ("POST", f"/admin/v1/ugc-images/{ASSET}/reject", {"json": {"reason_code": "other", **REASON}}),
]


@pytest.fixture
async def stub_client() -> AsyncIterator[tuple[httpx.AsyncClient, TestTokenIssuer]]:
    issuer = TestTokenIssuer()
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        app = create_app(
            DEFINITION,
            infra,
            jwt_verifier=issuer.verifier(),
            service_jwt_verifier=issuer.verifier(audience="media_pipeline"),
        )
        async with service_client(app) as client:
            yield client, issuer
    finally:
        await infra.close()


@pytest.mark.parametrize(("method", "path", "kwargs"), ADMIN_STUBS)
async def test_moderation_stubs_need_ugc_permission_and_answer_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    client, issuer = stub_client

    async def call(token: str | None) -> httpx.Response:
        headers = dict(KEY)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await client.request(method, path, headers=headers, **kwargs)

    assert_problem(await call(None), 401, "auth.unauthenticated")
    for token in (
        issuer.issue(),
        issuer.issue(perms=["admin.access"]),
        issuer.issue(perms=[PERM]),
        # право загрузки исходников не открывает модерацию UGC
        issuer.issue(perms=["admin.access", "media.upload_source"]),
    ):
        assert_problem(await call(token), 403, "auth.forbidden")
    allowed = await call(issuer.issue(perms=["admin.access", PERM]))
    assert_problem(allowed, 501, "common.not_implemented")


async def test_ingest_needs_service_token_with_ugc_scope_and_answers_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    """Приём UGC - только сервис с областью `ugc.image`; пользовательский токен не годится."""
    client, issuer = stub_client

    async def call(token: str | None) -> httpx.Response:
        headers = dict(KEY)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return await client.post(
            "/internal/v1/ugc-images",
            headers=headers,
            data={"owner_user_id": OWNER, "purpose": "avatar"},
            files={"file": ("a.png", PNG, "image/png")},
        )

    assert_problem(await call(None), 401, "auth.unauthenticated")
    assert (await call(issuer.issue(perms=["admin.access", PERM]))).status_code in (401, 403)
    link_only = issuer.issue_service("game", "media_pipeline", ["assets.link"])
    assert (await call(link_only)).status_code == 403
    wrong_audience = issuer.issue_service("progression", "identity", ["ugc.image"])
    assert (await call(wrong_audience)).status_code in (401, 403)
    progression = issuer.issue_service("progression", "media_pipeline", ["ugc.image"])
    assert_problem(await call(progression), 501, "common.not_implemented")
