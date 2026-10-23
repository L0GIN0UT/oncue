"""Заглушки 5b (шаг 0a, ADR 0045, 0048): доступ по контракту, до потоков S1/S3/S4 - 501."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from progression_service.main import DEFINITION
from progression_service.settings import Settings

from gv_service import Infrastructure, create_app
from gv_testkit import TestTokenIssuer, assert_problem, service_client

pytestmark = pytest.mark.component

UID = "0192a3b4-c5d6-7e8f-9a0b-0000000b0001"
ITEM = "0192a3b4-c5d6-7e8f-9a0b-0000000c0001"
KEY = {"Idempotency-Key": "idem-0192a3b4c5d6"}
REASON = {"reason": "проверка контракта"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

PUBLIC_STUBS: list[tuple[str, str, dict[str, Any]]] = [
    ("GET", "/v1/shop/items", {}),
    ("POST", f"/v1/shop/items/{ITEM}/purchase", {}),
    ("GET", "/v1/shop/packs", {}),
    ("GET", "/v1/me/inventory", {}),
    ("GET", f"/v1/appearance?user_ids={UID}", {}),
    ("GET", "/v1/me/appearance", {}),
    ("PUT", "/v1/me/appearance/frame", {"json": {"item_id": ITEM}}),
    ("PUT", "/v1/me/appearance/frame", {"json": {"item_id": None}}),
    ("POST", "/v1/promo/redeem", {"json": {"code": "ABCD-EFGH-JKLM"}}),
    ("GET", "/v1/me/avatar", {}),
    ("POST", "/v1/me/avatar", {"files": {"file": ("a.png", PNG, "image/png")}}),
]
ITEM_BODY = {
    "key": "frame_gold",
    "slot": "frame",
    "rarity": "rare",
    "title": "Золотая рамка",
    "media_kind": "animated",
    "price": 30,
    **REASON,
}
PACK_BODY = {
    "key": "winter",
    "title": "Зимний набор",
    "style": "winter",
    "item_ids": [ITEM],
    "price_kopecks": 19900,
    **REASON,
}
PROMO_BODY = {
    "label": "Весна",
    "reward": {"kind": "verses", "verses_amount": 50},
    "count": 3,
    "max_uses": 1,
    **REASON,
}
ITEMS = "progression.items.manage"
PROMO = "progression.promo.manage"
ADMIN_STUBS: list[tuple[str, str, dict[str, Any], str]] = [
    ("GET", "/admin/v1/items", {}, ITEMS),
    ("POST", "/admin/v1/items", {"json": ITEM_BODY}, ITEMS),
    ("PUT", f"/admin/v1/items/{ITEM}", {"json": ITEM_BODY}, ITEMS),
    (
        "PUT",
        f"/admin/v1/items/{ITEM}/media",
        {"data": REASON, "files": {"file": ("a.webp", b"RIFF", "image/webp")}},
        ITEMS,
    ),
    ("GET", "/admin/v1/packs", {}, ITEMS),
    ("POST", "/admin/v1/packs", {"json": PACK_BODY}, ITEMS),
    ("PUT", f"/admin/v1/packs/{ITEM}", {"json": PACK_BODY}, ITEMS),
    ("GET", "/admin/v1/promo-codes", {}, PROMO),
    ("POST", "/admin/v1/promo-codes", {"json": PROMO_BODY}, PROMO),
    ("POST", f"/admin/v1/promo-codes/{ITEM}/revoke", {"json": REASON}, PROMO),
]


@pytest.fixture
async def stub_client() -> AsyncIterator[tuple[httpx.AsyncClient, TestTokenIssuer]]:
    issuer = TestTokenIssuer()
    infra = await Infrastructure.open(Settings(), DEFINITION.tables)
    try:
        app = create_app(DEFINITION, infra, jwt_verifier=issuer.verifier())
        async with service_client(app) as client:
            yield client, issuer
    finally:
        await infra.close()


@pytest.mark.parametrize(("method", "path", "kwargs"), PUBLIC_STUBS)
async def test_public_5b_stubs_need_account_and_answer_501(
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
    assert_problem(await call(issuer.issue_guest()), 403, "auth.guest_forbidden")
    assert_problem(await call(issuer.issue()), 501, "common.not_implemented")


@pytest.mark.parametrize(("method", "path", "kwargs", "perm"), ADMIN_STUBS)
async def test_admin_5b_stubs_need_specific_permission_and_answer_501(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    kwargs: dict[str, Any],
    perm: str,
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
        issuer.issue(perms=[perm]),
        # право другого раздела не открывает этот: предметы и промокоды - раздельные права
        issuer.issue(perms=["admin.access", PROMO if perm == ITEMS else ITEMS]),
    ):
        assert_problem(await call(token), 403, "auth.forbidden")
    allowed = await call(issuer.issue(perms=["admin.access", perm]))
    assert_problem(allowed, 501, "common.not_implemented")


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("POST", f"/v1/shop/items/{ITEM}/purchase", {}),
        ("POST", "/v1/promo/redeem", {"json": {"code": "ABCD-EFGH-JKLM"}}),
        ("POST", "/v1/me/avatar", {"files": {"file": ("a.png", PNG, "image/png")}}),
    ],
)
async def test_public_mutations_require_idempotency_key(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    kwargs: dict[str, Any],
) -> None:
    client, issuer = stub_client
    headers = {"Authorization": f"Bearer {issuer.issue()}"}
    response = await client.request(method, path, headers=headers, **kwargs)
    assert response.status_code == 422
    assert response.json()["code"] != "common.not_implemented"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/v1/me/appearance/frame", {"item_id": ITEM, "price": 1}),
        ("POST", "/v1/promo/redeem", {"code": "ABCD-EFGH-JKLM", "reward": {"verses": 999}}),
    ],
)
async def test_public_bodies_reject_extra_fields_such_as_price_or_reward(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    """Цену и награду определяет сервер: лишнее поле клиента - 422, а не молчаливый пропуск."""
    client, issuer = stub_client
    headers = {"Authorization": f"Bearer {issuer.issue()}", **KEY}
    response = await client.request(method, path, headers=headers, json=body)
    assert response.status_code == 422


async def test_admin_mutations_require_reason(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    client, issuer = stub_client
    headers = {"Authorization": f"Bearer {issuer.issue(perms=['admin.access', ITEMS])}", **KEY}
    body = {k: v for k, v in ITEM_BODY.items() if k != "reason"}
    response = await client.post("/admin/v1/items", headers=headers, json=body)
    assert response.status_code == 422


async def test_appearance_batch_is_limited_to_twelve_ids(
    stub_client: tuple[httpx.AsyncClient, TestTokenIssuer],
) -> None:
    client, issuer = stub_client
    ids = "&".join(f"user_ids=0192a3b4-c5d6-7e8f-9a0b-0000000b{n:04d}" for n in range(13))
    response = await client.get(
        f"/v1/appearance?{ids}", headers={"Authorization": f"Bearer {issuer.issue()}"}
    )
    assert response.status_code == 422
