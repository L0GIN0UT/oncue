"""UGC-изображения 5b (ADR 0048): HTTP-ручки приёма и модерации, доступ и коды ошибок.

Сервис собран на фейках портов (``tests/conftest.py``); аутентификация, права и обработчики
ошибок - настоящие (``create_app`` + ``TestTokenIssuer``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from media_pipeline_service.api.admin import build_admin_router
from media_pipeline_service.api.internal import build_internal_router
from media_pipeline_service.api.public import build_public_router
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.main import DEFINITION
from media_pipeline_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

OWNER = "0192a3b4-c5d6-7e8f-9a0b-0000000b0001"
UNKNOWN = "0192a3b4-c5d6-7e8f-9a0b-0000000c0999"
PERM = "media.ugc.moderate"
REASON = {"reason": "проверка контракта"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
Client = tuple[httpx.AsyncClient, TestTokenIssuer, Any]


@pytest.fixture
async def ugc_client(media: Any) -> AsyncIterator[Client]:
    issuer = TestTokenIssuer()
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)

    def routers(infra: Infrastructure, adapters: Any = None) -> list[Any]:
        return [
            build_public_router(infra),
            build_internal_router(media.issue_link, media.list_sets, media.ugc),
            build_admin_router(
                upload=media.upload,
                set_position=media.set_position,
                regenerate=media.regenerate,
                list_sets=media.list_sets,
                list_profiles=media.list_profiles,
                list_sources=media.list_sources,
                submit_package=media.submit_package,
                get_package=media.get_package,
                ugc=media.ugc,
                max_upload_bytes=1_000_000,
            ),
        ]

    definition = dataclasses.replace(DEFINITION, build_routers=routers)
    try:
        app = create_app(
            definition,
            infra,
            jwt_verifier=issuer.verifier(),
            service_jwt_verifier=issuer.verifier(audience="media_pipeline"),
        )
        async with service_client(app) as client:
            yield client, issuer, media
    finally:
        await infra.close()


def _service(issuer: TestTokenIssuer, client_id: str = "progression") -> str:
    return issuer.issue_service(client_id, "media_pipeline", ["ugc.image"])


def _moderator(issuer: TestTokenIssuer) -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.issue(perms=['admin.access', PERM])}"}


async def _ingest(
    client: httpx.AsyncClient,
    token: str | None,
    *,
    key: str | None = "idem-0192a3b4c5d6",
    data: bytes = PNG,
    purpose: str = "avatar",
    owner: str = OWNER,
    filename: str = "a.png",
    content_type: str = "image/png",
) -> httpx.Response:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if key is not None:
        headers["Idempotency-Key"] = key
    return await client.post(
        "/internal/v1/ugc-images",
        headers=headers,
        data={"owner_user_id": owner, "purpose": purpose},
        files={"file": (filename, data, content_type)},
    )


# ---------------------------------------------------------------- приём
async def test_ingest_requires_service_token_with_scope_and_allowed_client(
    ugc_client: Client,
) -> None:
    client, issuer, media = ugc_client
    assert_problem(await _ingest(client, None), 401, "auth.unauthenticated")
    user = issuer.issue(perms=["admin.access", PERM])
    assert (await _ingest(client, user)).status_code in (401, 403)
    link_only = issuer.issue_service("progression", "media_pipeline", ["assets.link"])
    assert (await _ingest(client, link_only)).status_code == 403
    wrong_audience = issuer.issue_service("progression", "identity", ["ugc.image"])
    assert (await _ingest(client, wrong_audience)).status_code in (401, 403)
    # Сервис с правильной областью, но не из allowed_clients назначения.
    assert_problem(await _ingest(client, _service(issuer, "game")), 403, "ugc.client_forbidden")
    assert media.db.ugc == {}


async def test_ingest_accepts_and_returns_pending_asset_no_store(ugc_client: Client) -> None:
    client, issuer, media = ugc_client
    response = await _ingest(client, _service(issuer))
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert (body["status"], body["purpose"], body["owner_user_id"]) == ("pending", "avatar", OWNER)
    assert body["version"] == 1 and body["reason_code"] is None
    # Ключи объектов S3 наружу не отдаются.
    assert not any("key" in name or "url" in name for name in body)
    assert len(media.db.ugc) == 1


async def test_ingest_ignores_client_filename_and_content_type(ugc_client: Client) -> None:
    client, issuer, media = ugc_client
    response = await _ingest(
        client,
        _service(issuer),
        filename="../../etc/passwd",
        content_type="image/svg+xml",
    )
    assert response.status_code == 202
    [key] = media.storage.keys(StorageArea.ASSETS)
    assert key.startswith("p/") and "passwd" not in key and ".." not in key


async def test_ingest_errors(ugc_client: Client) -> None:
    client, issuer, media = ugc_client
    token = _service(issuer)
    assert_problem(await _ingest(client, token, purpose="nope"), 422, "ugc.purpose_unknown")
    assert_problem(await _ingest(client, token, data=b"bad bytes"), 422, "ugc.invalid_image")
    limit = media.ugc_catalog.purpose("avatar").max_bytes
    too_large = await _ingest(client, token, data=b"x" * (limit + 1))
    assert_problem(too_large, 413, "ugc.too_large")
    assert too_large.json()["details"]["max_bytes"] == limit
    assert media.ugc_encoder.calls == 1  # слишком большой файл до кодека не дошёл
    assert_problem(
        await _ingest(client, token, owner="not-a-uuid"), 422, "request.validation_failed"
    )
    assert_problem(await _ingest(client, token, key=None), 422, "idempotency.key_required")
    assert media.db.ugc == {}


async def test_ingest_is_idempotent_per_owner_and_key(ugc_client: Client) -> None:
    client, issuer, media = ugc_client
    token = _service(issuer)
    first = (await _ingest(client, token)).json()
    second = (await _ingest(client, token)).json()
    assert second["asset_id"] == first["asset_id"]
    assert len(media.db.ugc) == 1 and media.ugc_encoder.calls == 1
    reused = await _ingest(client, token, data=b"another file")
    assert_problem(reused, 409, "idempotency.key_reused")


# ---------------------------------------------------------------- модерация
def _admin_calls(asset: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("GET", "/admin/v1/ugc-images/settings", {}),
        ("GET", "/admin/v1/ugc-images", {}),
        ("GET", f"/admin/v1/ugc-images/{asset}", {}),
        ("GET", f"/admin/v1/ugc-images/{asset}/preview", {}),
        ("POST", f"/admin/v1/ugc-images/{asset}/approve", {"json": REASON}),
        (
            "POST",
            f"/admin/v1/ugc-images/{asset}/reject",
            {"json": {"reason_code": "other", **REASON}},
        ),
    ]


async def test_moderation_needs_ugc_permission(ugc_client: Client) -> None:
    client, issuer, _ = ugc_client
    for method, path, kwargs in _admin_calls(UNKNOWN):

        async def call(token: str | None, method: str = method, path: str = path, kw: Any = kwargs):  # type: ignore[no-untyped-def]  # httpx.Response
            headers = {"Idempotency-Key": "idem-0192a3b4c5d6"}
            if token is not None:
                headers["Authorization"] = f"Bearer {token}"
            return await client.request(method, path, headers=headers, **kw)

        assert_problem(await call(None), 401, "auth.unauthenticated")
        for token in (
            issuer.issue(),
            issuer.issue(perms=["admin.access"]),
            issuer.issue(perms=[PERM]),
            issuer.issue(perms=["admin.access", "media.upload_source"]),
        ):
            assert_problem(await call(token), 403, "auth.forbidden")


async def test_settings_expose_limits_and_reason_codes(ugc_client: Client) -> None:
    client, issuer, _ = ugc_client
    response = await client.get("/admin/v1/ugc-images/settings", headers=_moderator(issuer))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert [r["code"] for r in body["reasons"]] == [
        "inappropriate",
        "copyright",
        "low_quality",
        "personal_data",
        "other",
    ]
    [avatar] = body["purposes"]
    assert avatar["purpose"] == "avatar" and avatar["max_bytes"] <= 1_500_000
    assert avatar["formats"] == ["jpeg", "png", "webp"]


async def test_queue_preview_approve_flow(ugc_client: Client) -> None:
    client, issuer, media = ugc_client
    first = (await _ingest(client, _service(issuer), key="idem-aaaaaaaaaaaa")).json()
    mod = _moderator(issuer)
    queue = (await client.get("/admin/v1/ugc-images", headers=mod)).json()
    assert [i["asset_id"] for i in queue["items"]] == [first["asset_id"]]
    assert queue["next_cursor"] is None
    assert_problem(
        await client.get("/admin/v1/ugc-images", headers=mod, params={"cursor": "!!"}),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await client.get("/admin/v1/ugc-images", headers=mod, params={"cursor": "AAAA"}),
        422,
        "ugc.cursor_invalid",
    )

    preview = await client.get(f"/admin/v1/ugc-images/{first['asset_id']}/preview", headers=mod)
    [key] = media.storage.keys(StorageArea.ASSETS)
    assert preview.status_code == 200
    assert preview.content == media.storage.objects[(StorageArea.ASSETS, key)][0]
    assert preview.headers["Content-Type"] == "image/webp"
    assert preview.headers["Cache-Control"] == "no-store"
    assert preview.headers["X-Content-Type-Options"] == "nosniff"
    assert preview.headers["Content-Disposition"] == "inline"
    assert key not in str(preview.headers)  # ключ объекта наружу не попадает

    approve_headers = {**mod, "Idempotency-Key": "idem-bbbbbbbbbbbb"}
    approved = await client.post(
        f"/admin/v1/ugc-images/{first['asset_id']}/approve", headers=approve_headers, json=REASON
    )
    assert approved.status_code == 200 and approved.headers["Cache-Control"] == "no-store"
    assert approved.json()["status"] == "approved" and approved.json()["moderator_id"]
    # повтор с тем же ключом - тот же ответ; новый ключ - 409
    again = await client.post(
        f"/admin/v1/ugc-images/{first['asset_id']}/approve", headers=approve_headers, json=REASON
    )
    assert again.json() == approved.json()
    other_key = {**mod, "Idempotency-Key": "idem-cccccccccccc"}
    assert_problem(
        await client.post(
            f"/admin/v1/ugc-images/{first['asset_id']}/approve", headers=other_key, json=REASON
        ),
        409,
        "ugc.not_pending",
    )
    done = (
        await client.get("/admin/v1/ugc-images", headers=mod, params={"status": "approved"})
    ).json()
    assert [i["asset_id"] for i in done["items"]] == [first["asset_id"]]
    assert len(media.db.events) == 1
    # одобренное изображение через предпросмотр не отдаётся (оно публично на /ugc/)
    assert_problem(
        await client.get(f"/admin/v1/ugc-images/{first['asset_id']}/preview", headers=mod),
        409,
        "ugc.not_pending",
    )


async def test_reject_and_takedown_rules(ugc_client: Client) -> None:
    client, issuer, media = ugc_client
    mod = _moderator(issuer)
    asset = (await _ingest(client, _service(issuer))).json()["asset_id"]
    url = f"/admin/v1/ugc-images/{asset}/reject"

    def headers(key: str) -> dict[str, str]:
        return {**mod, "Idempotency-Key": key}

    assert_problem(
        await client.post(
            url, headers=headers("idem-000000000001"), json={"reason_code": "bogus", **REASON}
        ),
        422,
        "ugc.reason_unknown",
    )
    assert_problem(
        await client.post(url, headers=mod, json={"reason_code": "other", **REASON}),
        422,
        "idempotency.key_required",
    )  # нет Idempotency-Key
    assert_problem(
        await client.post(
            url,
            headers=headers("idem-000000000002"),
            json={"reason_code": "other", "reason": ""},
        ),
        422,
        "request.validation_failed",
    )
    rejected = await client.post(
        url, headers=headers("idem-000000000003"), json={"reason_code": "copyright", **REASON}
    )
    assert rejected.status_code == 200
    assert (rejected.json()["status"], rejected.json()["reason_code"]) == ("rejected", "copyright")
    assert_problem(
        await client.post(
            url, headers=headers("idem-000000000004"), json={"reason_code": "other", **REASON}
        ),
        409,
        "ugc.not_pending",
    )
    assert_problem(
        await client.post(
            f"/admin/v1/ugc-images/{UNKNOWN}/reject",
            headers=headers("idem-000000000005"),
            json={"reason_code": "other", **REASON},
        ),
        404,
        "ugc.not_found",
    )
    assert [a.action for a in media.db.audit] == ["ugc.reject"]
    # отклонённому просмотр не отдаётся (только pending), несуществующему - 404
    assert_problem(
        await client.get(f"/admin/v1/ugc-images/{asset}/preview", headers=mod),
        409,
        "ugc.not_pending",
    )
    assert_problem(
        await client.get(f"/admin/v1/ugc-images/{UNKNOWN}/preview", headers=mod),
        404,
        "ugc.not_found",
    )


async def test_card_returns_image_in_any_status_without_storage_keys(ugc_client: Client) -> None:
    client, issuer, _ = ugc_client
    first = (await _ingest(client, _service(issuer), key="idem-cardcardcard")).json()
    mod = _moderator(issuer)
    card = await client.get(f"/admin/v1/ugc-images/{first['asset_id']}", headers=mod)
    assert card.status_code == 200
    assert card.headers["Cache-Control"] == "no-store"
    body = card.json()
    assert body == (await client.get("/admin/v1/ugc-images", headers=mod)).json()["items"][0]
    assert (body["status"], body["reason_code"], body["decided_at"]) == ("pending", None, None)
    assert "key" not in str(body).lower()

    decision = {"reason_code": "other", **REASON}
    rejected = await client.post(
        f"/admin/v1/ugc-images/{first['asset_id']}/reject",
        json=decision,
        headers={**mod, "Idempotency-Key": "idem-cardreject1"},
    )
    assert rejected.status_code == 200
    after = (await client.get(f"/admin/v1/ugc-images/{first['asset_id']}", headers=mod)).json()
    assert after == rejected.json()
    assert (after["status"], after["reason_code"]) == ("rejected", "other")
    assert after["moderator_id"] is not None
    assert after["version"] >= 1


async def test_card_of_unknown_asset_is_404(ugc_client: Client) -> None:
    client, issuer, _ = ugc_client
    assert_problem(
        await client.get(f"/admin/v1/ugc-images/{UNKNOWN}", headers=_moderator(issuer)),
        404,
        "ugc.not_found",
    )
