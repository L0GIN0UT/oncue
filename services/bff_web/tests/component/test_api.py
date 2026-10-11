"""bff_web через HTTP: источники — заглушки по их OpenAPI-контрактам, кэш — реальный Redis."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
import pytest
from bff_web_service.main import DEFINITION
from bff_web_service.settings import Settings
from pydantic import SecretStr
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_service import Infrastructure, create_app
from gv_testkit import InfraProvider, TestTokenIssuer, assert_problem, service_client, stub_service
from gv_testkit.stubs import ServiceStub

pytestmark = pytest.mark.component

GAME, CATALOG, IDENTITY = "http://game.test", "http://catalog.test", "http://identity.test"
SESSION = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000700")
ITEM = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a1")
PUBLIC = "public, max-age=30, stale-while-revalidate=60"
PRIVATE = "private, no-store"

MODES = [
    {
        "key": "music.daily.classic",
        "version": 1,
        "domain": "music",
        "title_key": "modes.music.daily.classic",
        "source_type": "daily",
        "clue_type": "audio_intro",
        "rounds": 5,
        "levels": [{"index": 1, "hint": "0.5s"}],
        "allow_guest": True,
        "presets": None,
    },
    {
        "key": "film.random.frame",
        "version": 2,
        "domain": "film",
        "title_key": "modes.film.random.frame",
        "source_type": "random",
        "clue_type": "frame_progressive",
        "rounds": 5,
        "levels": [],
        "allow_guest": True,
        "presets": [{"id": "y2000", "title_key": "presets.y2000"}],
        "future_field": "игнорируется",
    },
]
DAILY = [
    {
        "mode_key": "music.daily.classic",
        "day": "2026-10-10",
        "status": "finished",
        "session_id": str(SESSION),
        "score": 21,
        "max_score": 30,
        "next_reset_at": "2026-10-10T21:00:00Z",
    }
]
RESULT = {
    "score": 6,
    "max_score": 12,
    "share": {"text": "OnCue 🟩⬛"},
    "daily_stats": [{"index": 0, "exact_pct": 42.5}],
    "rounds": [
        {
            "index": 0,
            "outcome": "exact",
            "level": 1,
            "points": 6,
            "reveal": {
                "media_item_id": str(ITEM),
                "title": "Брат",
                "year": 1997,
                "persons": [],
                "final_clue": {"kind": "image", "url": "/media/a/x.jpg", "mime": "image/jpeg"},
            },
        },
        {"index": 1, "outcome": "void", "level": None, "points": 0, "reveal": None},
    ],
}
CARD = {
    "id": str(ITEM),
    "domain": "film",
    "kind": "movie",
    "title": "Брат",
    "original_title": None,
    "year": 1997,
    "subtitle": None,
    "genres": ["drama", "crime"],
    "persons": [{"person_id": str(ITEM), "name": "А. Балабанов", "role": "director", "order": 0}],
    "metrics": {"popularity": 0.9},
}
ME = {
    "id": str(SESSION),
    "nickname": "neo_2026",
    "email": "neo@example.com",
    "email_verified": True,
    "display_name": "Нео",
    "locale": None,
    "roles": ["user"],
    "perms": [],
    "linked_accounts": [],
    "consents": [],
}


@dataclass
class Stubs:
    game: ServiceStub
    catalog: ServiceStub
    identity: ServiceStub


@dataclass
class Bff:
    client: httpx.AsyncClient
    stubs: Stubs
    issuer: TestTokenIssuer

    def bearer(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stubs() -> Iterator[Stubs]:
    with ExitStack() as stack:
        yield Stubs(
            game=stack.enter_context(stub_service(GAME, service="game")),
            catalog=stack.enter_context(stub_service(CATALOG, service="catalog")),
            identity=stack.enter_context(stub_service(IDENTITY, service="identity")),
        )


@pytest.fixture
async def bff(
    infra: InfraProvider, stubs: Stubs, token_issuer: TestTokenIssuer
) -> AsyncIterator[Bff]:
    prefix = f"test:bff:{uuid7().hex[-12:]}:"
    settings = Settings(
        environment="ci",
        redis_url=SecretStr(infra.redis_url()),
        upstreams={
            "game_url": GAME,
            "catalog_url": CATALOG,
            "identity_url": IDENTITY,
            "timeout_s": 0.5,
            "breaker_failures": 3,
        },
        page_cache={"key_prefix": prefix},
    )
    runtime = await Infrastructure.open(settings, DEFINITION.tables)
    try:
        app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
        async with service_client(app) as client:
            yield Bff(client, stubs, token_issuer)
    finally:
        redis = Redis.from_url(infra.redis_url())
        keys = [k async for k in redis.scan_iter(match=f"{prefix}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await runtime.close()


def _walk(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)


async def test_meta_and_health(bff: Bff) -> None:
    assert (await bff.client.get("/v1/meta")).json() == {"service": "bff_web", "status": "active"}
    assert (await bff.client.get("/health/ready")).status_code == 200


# ----------------------------------------------------------------------------- главная
async def test_home_for_user_all_ok(bff: Bff) -> None:
    token = bff.issuer.issue()
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    daily = bff.stubs.game.route("GET", "/v1/daily").respond(200, json=DAILY)

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert "Cookie" in response.headers["vary"]
    blocks = response.json()["blocks"]
    assert blocks["modes"]["status"] == "ok"
    assert [m["key"] for m in blocks["modes"]["data"]] == [m["key"] for m in MODES]
    assert blocks["modes"]["data"][1]["presets"] == [{"id": "y2000", "title_key": "presets.y2000"}]
    assert blocks["dailies"] == {
        "status": "ok",
        "data": [
            {
                "mode_key": "music.daily.classic",
                "domain": "music",
                "title_key": "modes.music.daily.classic",
                "day": "2026-10-10",
                "status": "finished",
                "session_id": str(SESSION),
                "score": 21,
                "max_score": 30,
                "next_reset_at": "2026-10-10T21:00:00Z",
            }
        ],
    }
    assert daily.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_home_token_from_cookie_is_forwarded_as_bearer(bff: Bff) -> None:
    token = bff.issuer.issue_guest()
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    daily = bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])
    bff.client.cookies.set("gv_access", token)

    response = await bff.client.get("/v1/pages/home")

    assert response.status_code == 200
    assert response.json()["blocks"]["dailies"] == {"status": "ok", "data": []}
    assert daily.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_home_game_down_gives_unavailable_blocks_and_200(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(503)
    bff.stubs.game.route("GET", "/v1/daily").mock(side_effect=httpx.ConnectError("refused"))

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue_guest()))

    assert response.status_code == 200
    assert response.json() == {
        "blocks": {
            "dailies": {"status": "unavailable", "data": None},
            "modes": {"status": "unavailable", "data": None},
        }
    }


async def test_home_anonymous_is_public_and_modes_are_cached(bff: Bff) -> None:
    modes = bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    daily = bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])

    first = await bff.client.get("/v1/pages/home")
    second = await bff.client.get("/v1/pages/home")

    assert first.status_code == second.status_code == 200
    assert first.headers["cache-control"] == PUBLIC
    assert first.json()["blocks"]["dailies"]["status"] == "unavailable"
    assert first.json() == second.json()
    assert modes.call_count == 1  # второй раз — из Redis
    assert daily.call_count == 0  # аноним — личного блока нет
    assert "authorization" not in modes.calls.last.request.headers


async def test_expired_or_forged_token_is_anonymous_not_500(bff: Bff) -> None:
    from datetime import timedelta

    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    daily = bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])
    expired = bff.issuer.issue(ttl=timedelta(seconds=-120))
    forged = TestTokenIssuer().issue()

    for token in (expired, forged, "not-a-jwt"):
        session = await bff.client.get("/v1/session", headers=bff.bearer(token))
        assert session.status_code == 200
        assert session.json() == {"subject": None}
        page = await bff.client.get("/v1/pages/home", headers=bff.bearer(token))
        assert page.status_code == 200
        assert page.headers["cache-control"] == PUBLIC
    assert daily.call_count == 0  # чужой или истёкший токен дальше не уходит


# ----------------------------------------------------------------------------- субъект
async def test_session_guest_by_claim_without_identity(bff: Bff) -> None:
    me = bff.stubs.identity.route("GET", "/v1/me").respond(403)

    response = await bff.client.get("/v1/session", headers=bff.bearer(bff.issuer.issue_guest()))

    assert response.json() == {"subject": {"kind": "guest", "display_name": None}}
    assert response.headers["cache-control"] == PRIVATE
    assert me.call_count == 0


async def test_session_user_name_from_identity(bff: Bff) -> None:
    me = bff.stubs.identity.route("GET", "/v1/me").respond(200, json=ME)
    token = bff.issuer.issue()

    response = await bff.client.get("/v1/session", headers=bff.bearer(token))

    assert response.json() == {"subject": {"kind": "user", "display_name": "Нео"}}
    assert me.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_session_revoked_user_is_anonymous(bff: Bff) -> None:
    bff.stubs.identity.route("GET", "/v1/me").respond(
        401, json={"code": "auth.session_revoked", "status": 401}
    )
    response = await bff.client.get("/v1/session", headers=bff.bearer(bff.issuer.issue()))
    assert response.json() == {"subject": None}


async def test_session_anonymous(bff: Bff) -> None:
    response = await bff.client.get("/v1/session")
    assert response.json() == {"subject": None}


# ----------------------------------------------------------------------------- хаб
async def test_solo_page_by_domain(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    daily = bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])

    response = await bff.client.get(
        "/v1/pages/solo?domain=film", headers=bff.bearer(bff.issuer.issue_guest())
    )

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert [m["key"] for m in blocks["random"]["data"]] == ["film.random.frame"]
    assert blocks["daily"] == {"status": "ok", "data": []}
    assert daily.calls.last.request.url.params["domain"] == "film"
    bad = await bff.client.get("/v1/pages/solo?domain=books")
    assert_problem(bad, 422, "request.validation_failed")


# ----------------------------------------------------------------------------- итог
async def test_results_page_joins_catalog_and_caches_card(bff: Bff) -> None:
    token = bff.issuer.issue()
    result = bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT)
    card = bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)

    first = await bff.client.get(f"/v1/pages/results/{SESSION}", headers=bff.bearer(token))
    second = await bff.client.get(f"/v1/pages/results/{SESSION}", headers=bff.bearer(token))

    assert first.status_code == second.status_code == 200, first.text
    assert first.headers["cache-control"] == PRIVATE
    blocks = first.json()["blocks"]
    assert blocks["result"]["data"]["score"] == 6
    assert blocks["result"]["data"]["share_text"] == "OnCue 🟩⬛"
    assert [(r["media_item_id"], r["exact_pct"]) for r in blocks["result"]["data"]["rounds"]] == [
        (str(ITEM), 42.5),
        (None, None),
    ]
    assert blocks["items"]["data"][0]["genres"] == ["drama", "crime"]
    assert blocks["items"]["data"][0]["persons"] == [{"name": "А. Балабанов", "role": "director"}]
    assert "metrics" not in set(_walk(first.json()))
    assert result.call_count == 2  # итог личный — не кэшируется
    assert card.call_count == 1  # карточка catalog — из кэша
    assert "authorization" not in card.calls.last.request.headers  # карточка публичная


async def test_results_page_catalog_down_keeps_result(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(500)

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    assert response.json()["blocks"]["result"]["status"] == "ok"
    assert response.json()["blocks"]["items"] == {"status": "unavailable", "data": None}


async def test_results_page_foreign_session_is_404(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(
        404, json={"code": "game.session_not_found", "status": 404}
    )
    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue_guest())
    )
    assert_problem(response, 404, "game.session_not_found")


async def test_results_page_requires_token(bff: Bff) -> None:
    result = bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT)
    assert_problem(
        await bff.client.get(f"/v1/pages/results/{SESSION}"), 401, "auth.unauthenticated"
    )
    assert result.call_count == 0


async def test_upstream_contract_violation_is_unavailable_block(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=[{"key": 1}])
    response = await bff.client.get("/v1/pages/home")
    assert response.status_code == 200
    assert response.json()["blocks"]["modes"]["status"] == "unavailable"


async def test_breaker_stops_calling_failed_service(bff: Bff) -> None:
    modes = bff.stubs.game.route("GET", "/v1/modes").respond(503)
    for _ in range(5):
        assert (await bff.client.get("/v1/pages/home")).status_code == 200
    assert modes.call_count == 3  # breaker_failures = 3, дальше цепь разомкнута
