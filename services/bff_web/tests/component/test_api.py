"""bff_web через HTTP: источники — заглушки по их OpenAPI-контрактам, кэш — реальный Redis."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx
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
COLLECTIONS, LIBRARY = "http://collections.test", "http://library.test"
MATCH = "http://match.test"
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


MODES_3A = [
    *MODES,
    {
        "key": "film.marathon.director",
        "version": 1,
        "domain": "film",
        "title_key": "modes.film.marathon.director.title",
        "source_type": "marathon",
        "clue_type": "frame_progressive",
        "rounds": 200,
        "levels": [{"index": 1, "hint": {"blur_px": 24, "crop": 0.4}}],
        "allow_guest": True,
        "presets": None,
        "answer_format": "classic",
        "presentation": "streak",
        "start": {"subject": {"roles": ["director"], "max": 3}},
    },
    {
        "key": "film.pair.rating",
        "version": 1,
        "domain": "film",
        "title_key": "modes.film.pair.rating.title",
        "source_type": "pair",
        "clue_type": "frame_progressive",
        "rounds": 200,
        "levels": [{"index": 1, "hint": {"blur_px": 0, "crop": 1}}],
        "allow_guest": True,
        "presets": None,
        "answer_format": "pick",
        "presentation": "streak",
        "start": None,
    },
    {
        "key": "film.bracket.weekly",
        "version": 1,
        "domain": "film",
        "title_key": "modes.film.bracket.weekly.title",
        "source_type": "bracket",
        "clue_type": "frame_progressive",
        "rounds": 31,
        "levels": [{"index": 1, "hint": {"blur_px": 0, "crop": 1}}],
        "allow_guest": True,
        "presets": None,
        "answer_format": "pick",
        "presentation": "bracket",
        "start": None,
    },
]
WEEKLY = {
    "mode_key": "film.bracket.weekly",
    "day": "2026-10-12",
    "status": "in_progress",
    "session_id": str(SESSION),
    "score": None,
    "max_score": 0,
    "next_reset_at": "2026-10-18T21:00:00Z",
}
RECORDS = [
    {
        "mode_key": "film.pair.rating",
        "best": 14,
        "percentile": 87.5,
        "achieved_at": "2026-10-11T18:00:00Z",
    },
    {"mode_key": "music.pair.popularity", "best": 3, "achieved_at": "2026-10-09T10:00:00Z"},
]
RESULT_BRACKET = {
    "score": 0,
    "max_score": 0,
    "share": {"text": "OnCue Топ-32: Брат 🏆"},
    "daily_stats": None,
    "presentation": "bracket",
    "record": None,
    "bracket": {
        "champion": "Брат",
        "picks": [
            {"stage": 1, "winner": "Брат", "loser": "Жмурки"},
            {"stage": 5, "winner": "Брат", "loser": "Брат 2"},
        ],
    },
    "rounds": [],
}


@dataclass
class Stubs:
    game: ServiceStub
    catalog: ServiceStub
    identity: ServiceStub
    collections: ServiceStub
    library: ServiceStub
    match: ServiceStub


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
            collections=stack.enter_context(stub_service(COLLECTIONS, service="collections")),
            library=stack.enter_context(stub_service(LIBRARY, service="library")),
            match=stack.enter_context(stub_service(MATCH, service="match")),
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
            "collections_url": COLLECTIONS,
            "library_url": LIBRARY,
            "match_url": MATCH,
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
                "presentation": "score",
            }
        ],
    }
    assert blocks["weekly"] == {"status": "ok", "data": []}
    assert blocks["modes"]["data"][0]["answer_format"] == "classic"  # поле по умолчанию game
    assert blocks["modes"]["data"][0]["start"] is None
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
            "weekly": {"status": "unavailable", "data": None},
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
    assert blocks["records"] is None  # гость: рекордов нет
    assert daily.calls.last.request.url.params["domain"] == "film"
    bad = await bff.client.get("/v1/pages/solo?domain=books")
    assert_problem(bad, 422, "request.validation_failed")


async def test_solo_user_gets_showcase_weekly_and_records(bff: Bff) -> None:
    token = bff.issuer.issue()
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES_3A)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[WEEKLY])
    records = bff.stubs.game.route("GET", "/v1/me/records").respond(200, json=RECORDS)

    response = await bff.client.get("/v1/pages/solo?domain=film", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    assert [
        (g["source_type"], g["presentation"], [m["key"] for m in g["modes"]])
        for g in blocks["modes"]["data"]
    ] == [
        ("random", "score", ["film.random.frame"]),
        ("marathon", "streak", ["film.marathon.director"]),
        ("pair", "streak", ["film.pair.rating"]),
    ]
    marathon = blocks["modes"]["data"][1]["modes"][0]
    assert marathon["start"] == {"subject": {"roles": ["director"], "max": 3}}
    assert marathon["rounds"] == 200
    assert blocks["modes"]["data"][2]["modes"][0]["answer_format"] == "pick"
    assert blocks["daily"] == {"status": "ok", "data": []}  # сетка — не дейлик
    assert blocks["weekly"]["data"] == [
        {
            "mode_key": "film.bracket.weekly",
            "domain": "film",
            "title_key": "modes.film.bracket.weekly.title",
            "day": "2026-10-12",
            "status": "in_progress",
            "session_id": str(SESSION),
            "score": None,
            "max_score": 0,
            "next_reset_at": "2026-10-18T21:00:00Z",
            "presentation": "bracket",
        }
    ]
    # Рекорд музыкального режима отфильтрован доменом хаба.
    assert blocks["records"] == {
        "status": "ok",
        "data": [
            {
                "mode_key": "film.pair.rating",
                "domain": "film",
                "title_key": "modes.film.pair.rating.title",
                "presentation": "streak",
                "best": 14,
                "percentile": 87.5,
                "achieved_at": "2026-10-11T18:00:00Z",
            }
        ],
    }
    assert records.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_solo_guest_has_no_records_and_game_is_not_asked(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES_3A)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])
    records = bff.stubs.game.route("GET", "/v1/me/records").respond(200, json=[])

    guest = await bff.client.get("/v1/pages/solo", headers=bff.bearer(bff.issuer.issue_guest()))
    anonymous = await bff.client.get("/v1/pages/solo")

    assert guest.json()["blocks"]["records"] is None
    assert guest.headers["cache-control"] == PRIVATE
    assert anonymous.json()["blocks"]["records"] is None
    assert anonymous.headers["cache-control"] == PUBLIC
    assert records.call_count == 0


async def test_solo_game_down_gives_unavailable_blocks_and_200(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(503)
    bff.stubs.game.route("GET", "/v1/daily").mock(side_effect=httpx.ConnectError("refused"))
    bff.stubs.game.route("GET", "/v1/me/records").respond(500)

    response = await bff.client.get("/v1/pages/solo", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    unavailable = {"status": "unavailable", "data": None}
    assert response.json() == {
        "blocks": dict.fromkeys(("daily", "random", "modes", "weekly", "records"), unavailable)
    }


async def test_solo_records_down_keeps_other_blocks(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES_3A)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[*DAILY, WEEKLY])
    bff.stubs.game.route("GET", "/v1/me/records").respond(429)

    response = await bff.client.get("/v1/pages/solo", headers=bff.bearer(bff.issuer.issue()))

    blocks = response.json()["blocks"]
    assert response.status_code == 200
    assert blocks["records"] == {"status": "unavailable", "data": None}
    assert [c["mode_key"] for c in blocks["daily"]["data"]] == ["music.daily.classic"]
    assert [c["mode_key"] for c in blocks["weekly"]["data"]] == ["film.bracket.weekly"]
    assert blocks["modes"]["status"] == "ok"


async def test_home_weekly_bracket_does_not_break_daily_cards(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES_3A)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[*DAILY, WEEKLY])

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue_guest()))

    blocks = response.json()["blocks"]
    assert [c["mode_key"] for c in blocks["dailies"]["data"]] == ["music.daily.classic"]
    assert [(c["mode_key"], c["max_score"]) for c in blocks["weekly"]["data"]] == [
        ("film.bracket.weekly", 0)
    ]
    assert len(blocks["modes"]["data"]) == len(MODES_3A)


# ----------------------------------------------------------------------------- итог
async def test_results_page_bracket_presentation_and_record(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT_BRACKET)

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200, response.text
    summary = response.json()["blocks"]["result"]["data"]
    assert summary["presentation"] == "bracket"
    assert summary["record"] is None
    assert summary["bracket"] == RESULT_BRACKET["bracket"]
    assert response.json()["blocks"]["items"] == {"status": "ok", "data": []}


async def test_results_page_streak_record(bff: Bff) -> None:
    streak = {
        **RESULT,
        "daily_stats": None,
        "presentation": "streak",
        "record": {"best": 14, "is_new": True, "percentile": 87.5},
        "bracket": None,
    }
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=streak)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    summary = response.json()["blocks"]["result"]["data"]
    assert summary["presentation"] == "streak"
    assert summary["record"] == {"best": 14, "is_new": True, "percentile": 87.5}
    assert summary["bracket"] is None


async def test_results_page_stage2_result_defaults_to_score(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    summary = response.json()["blocks"]["result"]["data"]
    assert (summary["presentation"], summary["record"], summary["bracket"]) == ("score", None, None)


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


# ----------------------------------------------------------------------------- этап 3b
COLLECTION_ID = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000c01")
SHELF_ID = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000d01")


def collection_card(title: str, **extra: Any) -> dict[str, Any]:
    return {
        "collection_id": str(COLLECTION_ID),
        "domain": "film",
        "kind": "ugc",
        "title": title,
        "cover": {"preset": "sunset"},
        "tags": ["cinema"],
        "author": {"kind": "user", "user_id": str(SESSION), "nickname": "neo_2026"},
        "default_mode_key": "film.collection.frame",
        "items_count": 8,
        "likes_count": 3,
        "plays_count": 12,
        "difficulty": None,
        "featured": False,
        "published_at": "2026-10-16T10:00:00Z",
        **extra,
    }


COLLECTION_DETAIL = {
    **collection_card("Кино · кадры"),
    "description": "Восемь фильмов",
    "visibility": "public",
    "status": "published",
    "version": 2,
    "items_visible": True,
    "items": [
        {
            "position": 0,
            "media_item_id": str(ITEM),
            "title": "Брат",
            "year": 1997,
            "available": True,
            "custom": {"emoji": "🔫🧥🎸🚋❄️🇷🇺"},
        }
    ],
    "me": {"liked": True, "favorite": False, "completed": True, "is_author": False},
    "future_field": "игнорируется",
}
COLLECTION_MODES = [
    {
        "mode_key": "film.collection.frame",
        "title_key": "modes.film.collection.frame.title",
        "clue_type": "frame_progressive",
        "allow_guest": True,
        "playable": 7,
        "total": 8,
        "available": True,
        "round_options": [5, "all"],
    }
]
SHELF = {
    "ref": "system:watch_later",
    "shelf_id": None,
    "kind": "system",
    "key": "watch_later",
    "title": None,
    "title_key": "library.shelf.watch_later",
    "domains": ["film"],
    "exclusive_with": ["system:watched"],
    "items_count": 1,
    "updated_at": "2026-10-16T10:00:00Z",
}
CUSTOM_SHELF = {
    **SHELF,
    "ref": str(SHELF_ID),
    "shelf_id": str(SHELF_ID),
    "kind": "custom",
    "key": None,
    "title": "Ужасы 80-х",
    "title_key": None,
    "domains": ["music", "film"],
    "exclusive_with": [],
}
SHELF_ITEMS = {
    "shelf": SHELF,
    "items": [
        {
            "media_item_id": str(ITEM),
            "domain": "film",
            "kind": "movie",
            "title": "Брат",
            "year": 1997,
            "persons": ["Алексей Балабанов"],
            "status": "unavailable",
            "source": "reveal",
            "added_at": "2026-10-16T10:00:00Z",
        }
    ],
    "next_cursor": "c2",
}


def _by_sort(request: httpx.Request) -> httpx.Response:
    sort = request.url.params["sort"]
    return httpx.Response(
        200, json={"items": [collection_card(sort)], "next_cursor": f"{sort}-next"}
    )


async def test_collections_page_showcases_are_public_and_cached(bff: Bff) -> None:
    catalog = bff.stubs.collections.route("GET", "/v1/collections").mock(side_effect=_by_sort)

    first = await bff.client.get(
        "/v1/pages/collections?domain=film&curated=true", headers=bff.bearer(bff.issuer.issue())
    )
    second = await bff.client.get("/v1/pages/collections?domain=film&curated=true")

    assert first.status_code == second.status_code == 200, first.text
    assert first.headers["cache-control"] == PUBLIC  # каталог одинаков и для вошедшего
    assert "Cookie" in first.headers["vary"]
    blocks = first.json()["blocks"]
    assert blocks["results"] is None
    for name in ("featured", "popular", "new"):
        assert blocks[name]["status"] == "ok"
        assert blocks[name]["data"]["next_cursor"] == f"{name}-next"
        assert blocks[name]["data"]["items"][0]["title"] == name
    assert blocks["featured"]["data"]["items"][0]["author"] == {
        "kind": "user",
        "user_id": str(SESSION),
        "nickname": "neo_2026",
    }
    assert first.json() == second.json()
    assert catalog.call_count == 3  # второй раз — из Redis
    sent = [dict(c.request.url.params) for c in catalog.calls]
    assert sorted(p["sort"] for p in sent) == ["featured", "new", "popular"]
    assert all(p["domain"] == "film" and p["curated"] == "true" for p in sent)
    assert all(p["limit"] == "12" for p in sent)
    assert all("authorization" not in c.request.headers for c in catalog.calls)


async def test_collections_page_search_is_results_only_and_not_cached(bff: Bff) -> None:
    catalog = bff.stubs.collections.route("GET", "/v1/collections").mock(side_effect=_by_sort)

    for _ in range(2):
        response = await bff.client.get("/v1/pages/collections?q=кадры&sort=new&domain=film")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == PRIVATE

    body = response.json()["blocks"]
    assert body["featured"] is None and body["popular"] is None and body["new"] is None
    assert body["results"]["status"] == "ok"
    assert body["results"]["data"]["items"][0]["title"] == "new"
    assert catalog.call_count == 2
    assert dict(catalog.calls.last.request.url.params) == {
        "q": "кадры",
        "sort": "new",
        "domain": "film",
        "limit": "20",
    }


async def test_collections_page_cursor_continues_showcase(bff: Bff) -> None:
    catalog = bff.stubs.collections.route("GET", "/v1/collections").mock(side_effect=_by_sort)
    response = await bff.client.get("/v1/pages/collections?sort=new&cursor=new-next")
    assert response.status_code == 200
    assert response.headers["cache-control"] == PUBLIC
    assert response.json()["blocks"]["results"]["data"]["next_cursor"] == "new-next"
    assert catalog.calls.last.request.url.params["cursor"] == "new-next"


async def test_collections_page_collections_down_is_200_unavailable(bff: Bff) -> None:
    bff.stubs.collections.route("GET", "/v1/collections").respond(503)
    response = await bff.client.get("/v1/pages/collections")
    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert {blocks[n]["status"] for n in ("featured", "popular", "new")} == {"unavailable"}
    searched = await bff.client.get("/v1/pages/collections?q=x")
    assert searched.json()["blocks"]["results"] == {"status": "unavailable", "data": None}


async def test_collections_page_foreign_cursor_is_422(bff: Bff) -> None:
    bff.stubs.collections.route("GET", "/v1/collections").respond(
        422, json={"code": "collections.cursor_invalid", "title": "bad cursor", "status": 422}
    )
    response = await bff.client.get("/v1/pages/collections?sort=likes&cursor=new-next")
    assert_problem(response, 422, "collections.cursor_invalid")


async def test_collection_page_for_user(bff: Bff) -> None:
    token = bff.issuer.issue()
    detail = bff.stubs.collections.route("GET", f"/v1/collections/{COLLECTION_ID}").respond(
        200, json=COLLECTION_DETAIL
    )
    modes = bff.stubs.game.route("GET", f"/v1/collections/{COLLECTION_ID}/modes").respond(
        200, json=COLLECTION_MODES
    )

    response = await bff.client.get(
        f"/v1/pages/collections/{COLLECTION_ID}", headers=bff.bearer(token)
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    body = response.json()
    assert body["me"] == {"liked": True, "favorite": False, "completed": True, "is_author": False}
    card = body["blocks"]["collection"]["data"]
    assert body["blocks"]["collection"]["status"] == "ok"
    assert (card["version"], card["items_visible"], card["status"]) == (2, True, "published")
    assert card["items"][0]["custom"] == {"emoji": "🔫🧥🎸🚋❄️🇷🇺"}
    assert "me" not in card and "future_field" not in card
    assert body["blocks"]["modes"] == {"status": "ok", "data": COLLECTION_MODES}
    assert detail.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert "authorization" not in modes.calls.last.request.headers  # режимы не личные


async def test_collection_page_guest_and_anonymous_get_no_me(bff: Bff) -> None:
    bff.stubs.collections.route("GET", f"/v1/collections/{COLLECTION_ID}").respond(
        200, json={**COLLECTION_DETAIL, "items_visible": False, "items": None}
    )
    modes = bff.stubs.game.route("GET", f"/v1/collections/{COLLECTION_ID}/modes").respond(
        200, json=COLLECTION_MODES
    )

    guest = await bff.client.get(
        f"/v1/pages/collections/{COLLECTION_ID}", headers=bff.bearer(bff.issuer.issue_guest())
    )
    anonymous = await bff.client.get(f"/v1/pages/collections/{COLLECTION_ID}")

    assert guest.status_code == anonymous.status_code == 200
    assert guest.json()["me"] is None and anonymous.json()["me"] is None
    assert guest.headers["cache-control"] == PRIVATE
    assert anonymous.headers["cache-control"] == PUBLIC
    assert anonymous.json()["blocks"]["collection"]["data"]["items"] is None
    assert modes.call_count == 1  # второй раз режимы сборника — из Redis


async def test_collection_page_game_down_keeps_card(bff: Bff) -> None:
    bff.stubs.collections.route("GET", f"/v1/collections/{COLLECTION_ID}").respond(
        200, json=COLLECTION_DETAIL
    )
    bff.stubs.game.route("GET", f"/v1/collections/{COLLECTION_ID}/modes").respond(503)

    response = await bff.client.get(
        f"/v1/pages/collections/{COLLECTION_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["collection"]["status"] == "ok"
    assert blocks["modes"] == {"status": "unavailable", "data": None}
    assert response.json()["me"]["completed"] is True


async def test_collection_page_collections_down_is_200_unavailable(bff: Bff) -> None:
    bff.stubs.collections.route("GET", f"/v1/collections/{COLLECTION_ID}").mock(
        side_effect=httpx.ConnectError("refused")
    )
    bff.stubs.game.route("GET", f"/v1/collections/{COLLECTION_ID}/modes").respond(
        200, json=COLLECTION_MODES
    )
    response = await bff.client.get(f"/v1/pages/collections/{COLLECTION_ID}")
    assert response.status_code == 200
    assert response.json()["blocks"]["collection"] == {"status": "unavailable", "data": None}
    assert response.json()["blocks"]["modes"]["status"] == "ok"
    assert response.json()["me"] is None


async def test_collection_page_hidden_or_missing_is_404(bff: Bff) -> None:
    bff.stubs.collections.route("GET", f"/v1/collections/{COLLECTION_ID}").respond(
        404, json={"code": "collections.not_found", "title": "Not found", "status": 404}
    )
    bff.stubs.game.route("GET", f"/v1/collections/{COLLECTION_ID}/modes").respond(
        404, json={"code": "game.collection_not_found", "title": "Not found", "status": 404}
    )
    response = await bff.client.get(f"/v1/pages/collections/{COLLECTION_ID}")
    assert_problem(response, 404, "collections.not_found")


async def test_library_page_for_user(bff: Bff) -> None:
    token = bff.issuer.issue()
    shelves = bff.stubs.library.route("GET", "/v1/shelves").respond(200, json=[SHELF, CUSTOM_SHELF])

    response = await bff.client.get("/v1/pages/library", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert response.json() == {
        "blocks": {"shelves": {"status": "ok", "data": [SHELF, CUSTOM_SHELF]}}
    }
    assert shelves.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_library_pages_require_user(bff: Bff) -> None:
    shelves = bff.stubs.library.route("GET", "/v1/shelves").respond(200, json=[SHELF])
    items = bff.stubs.library.route("GET", "/v1/shelves/system:watched/items").respond(
        200, json=SHELF_ITEMS
    )
    guest = bff.bearer(bff.issuer.issue_guest())

    assert_problem(await bff.client.get("/v1/pages/library"), 401, "auth.unauthenticated")
    assert_problem(
        await bff.client.get("/v1/pages/library", headers=guest), 403, "auth.guest_forbidden"
    )
    assert_problem(
        await bff.client.get("/v1/pages/library/system:watched", headers=guest),
        403,
        "auth.guest_forbidden",
    )
    assert shelves.call_count == 0 and items.call_count == 0


async def test_library_down_is_200_unavailable(bff: Bff) -> None:
    bff.stubs.library.route("GET", "/v1/shelves").respond(503)
    response = await bff.client.get("/v1/pages/library", headers=bff.bearer(bff.issuer.issue()))
    assert response.status_code == 200
    assert response.json() == {"blocks": {"shelves": {"status": "unavailable", "data": None}}}


async def test_library_shelf_page(bff: Bff) -> None:
    items = bff.stubs.library.route("GET", "/v1/shelves/system:watch_later/items").respond(
        200, json=SHELF_ITEMS
    )

    response = await bff.client.get(
        "/v1/pages/library/system:watch_later?cursor=c1", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert response.json() == {"blocks": {"shelf": {"status": "ok", "data": SHELF_ITEMS}}}
    assert dict(items.calls.last.request.url.params) == {"cursor": "c1", "limit": "50"}


async def test_library_foreign_shelf_is_404(bff: Bff) -> None:
    bff.stubs.library.route("GET", f"/v1/shelves/{SHELF_ID}/items").respond(
        404, json={"code": "library.shelf_not_found", "title": "Not found", "status": 404}
    )
    response = await bff.client.get(
        f"/v1/pages/library/{SHELF_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    assert_problem(response, 404, "library.shelf_not_found")


@pytest.mark.parametrize(
    "path",
    [
        "/v1/pages/library/system:Bad-Key",
        "/v1/pages/library/123",
        "/v1/pages/collections/not-a-uuid",
        "/v1/pages/collections?sort=random",
        "/v1/pages/collections?mode=../admin",
        "/v1/pages/collections?cursor=a%20b",
    ],
)
async def test_collection_and_library_page_params_are_strict(bff: Bff, path: str) -> None:
    assert (await bff.client.get(path)).status_code == 422


# ----------------------------------------------------------------------------- онлайн (4a)
SAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "data" / "match.json").read_text(encoding="utf-8")
)
MATCH_ID = UUID(SAMPLES["match_result"]["match_id"])
ITEM_2 = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a2")
UNAVAILABLE = {"status": "unavailable", "data": None}


def _online_routes(bff: Bff) -> tuple[respx.Route, respx.Route, respx.Route]:
    rooms = bff.stubs.match.route("GET", "/v1/rooms/open").respond(200, json=SAMPLES["open_rooms"])
    matches = bff.stubs.match.route("GET", "/v1/me/matches").respond(
        200, json=SAMPLES["my_matches"]
    )
    modes = bff.stubs.game.route("GET", "/v1/modes").respond(200, json=SAMPLES["online_modes"])
    return rooms, matches, modes


async def test_online_page_for_user_all_blocks(bff: Bff) -> None:
    token = bff.issuer.issue()
    rooms, matches, modes = _online_routes(bff)

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    body = response.json()
    assert body["status"] == "ok"
    blocks = body["blocks"]
    assert blocks["open_rooms"]["status"] == "ok"
    room = blocks["open_rooms"]["data"]["items"][0]
    assert room["code"] == "K7M2QX9P"
    assert room["mode"]["kind"] == "lobby"
    assert room["mode"]["ranked"] is False  # поле по умолчанию match
    assert room["settings"]["collection_ids"] == ["0192a3b4-c5d6-7e8f-9a0b-000000000c01"]
    assert blocks["open_rooms"]["data"]["next_cursor"] == "c2Vjb25k"
    assert blocks["my_matches"] == {"status": "ok", "data": SAMPLES["my_matches"]}
    online_modes = blocks["modes"]["data"]
    assert [m["key"] for m in online_modes] == ["film.duel.frame", "film.lobby.collection"]
    assert online_modes[0]["online"] == {
        "kind": "duel",
        "players_min": 2,
        "players_max": 2,
        "rounds": 7,
        "level_windows_s": [15, 15, 15],
        "contest_type": "race",
        "ranked": False,
        "chat": {
            "emotes": ["match.emotes.gg"],
            "phrases": ["match.phrases.nice"],
            "free_text": False,
        },
        "ladder": None,
        "max_collections": None,
        "lobby": None,
    }
    assert online_modes[1]["online"]["lobby"] == SAMPLES["online_modes"][1]["online"]["lobby"]
    assert online_modes[1]["online"]["max_collections"] == 3
    assert "allow_guest" not in online_modes[0]  # онлайн только с аккаунтом (D13)
    assert rooms.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert matches.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert dict(rooms.calls.last.request.url.params) == {"limit": "20"}
    assert dict(matches.calls.last.request.url.params) == {"limit": "10"}
    assert dict(modes.calls.last.request.url.params) == {"session": "online"}
    assert "authorization" not in modes.calls.last.request.headers  # публичный, кэшируемый


async def test_online_page_guest_and_anonymous_get_auth_required_without_calls(bff: Bff) -> None:
    rooms, matches, modes = _online_routes(bff)

    for headers in ({}, bff.bearer(bff.issuer.issue_guest())):
        response = await bff.client.get("/v1/pages/online", headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == PRIVATE
        assert response.json() == {"status": "auth_required", "blocks": None}
    assert rooms.call_count == matches.call_count == modes.call_count == 0


@pytest.mark.parametrize("failure", ["5xx", "timeout", "contract"])
async def test_online_page_match_down_degrades_its_block_only(bff: Bff, failure: str) -> None:
    rooms, _, _ = _online_routes(bff)
    if failure == "5xx":
        rooms.respond(503)
    elif failure == "timeout":
        rooms.mock(side_effect=httpx.ReadTimeout("slow"))
    else:
        rooms.respond(200, json={"items": [{"code": "bad"}]})

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["open_rooms"] == UNAVAILABLE
    assert blocks["my_matches"]["status"] == "ok"
    assert blocks["modes"]["status"] == "ok"


async def test_online_page_match_stub_501_and_game_down_are_unavailable(bff: Bff) -> None:
    """match до реализации отвечает 501 — блоки гаснут, страница 200."""
    problem = {"code": "core.not_implemented", "title": "Not implemented", "status": 501}
    bff.stubs.match.route("GET", "/v1/rooms/open").respond(501, json=problem)
    bff.stubs.match.route("GET", "/v1/me/matches").respond(501, json=problem)
    bff.stubs.game.route("GET", "/v1/modes").respond(503)

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "blocks": {"open_rooms": UNAVAILABLE, "my_matches": UNAVAILABLE, "modes": UNAVAILABLE},
    }


async def test_online_modes_are_cached_under_own_key(bff: Bff) -> None:
    _, _, online = _online_routes(bff)
    token = bff.bearer(bff.issuer.issue())

    await bff.client.get("/v1/pages/online", headers=token)
    await bff.client.get("/v1/pages/online", headers=token)
    assert online.call_count == 1  # второй раз — из Redis

    await bff.client.get("/v1/pages/home")
    assert online.call_count == 2  # витрина соло не берёт онлайн-режимы из кэша
    assert "session" not in online.calls.last.request.url.params


# ----------------------------------------------------------------------------- итог матча
def _match_routes(bff: Bff) -> tuple[respx.Route, respx.Route, respx.Route]:
    result = bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json=SAMPLES["match_result"]
    )
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)
    item_2 = bff.stubs.catalog.route("GET", f"/v1/items/{ITEM_2}").respond(
        200, json={**CARD, "id": str(ITEM_2), "title": "Брат 2"}
    )
    shelves_1 = bff.stubs.library.route("GET", f"/v1/items/{ITEM}/shelves").respond(
        200, json=SAMPLES["item_shelves"]
    )
    bff.stubs.library.route("GET", f"/v1/items/{ITEM_2}/shelves").respond(
        200, json={"media_item_id": str(ITEM_2), "shelves": []}
    )
    return result, item_2, shelves_1


async def test_match_page_result_items_and_library_flags(bff: Bff) -> None:
    token = bff.issuer.issue()
    result, _, shelves_1 = _match_routes(bff)

    response = await bff.client.get(f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    assert blocks["match"] == {"status": "ok", "data": SAMPLES["match_result"]}
    # Раунды 0 и 2 — одна единица: карточка и флаг по ней — по одному разу.
    assert [i["title"] for i in blocks["items"]["data"]] == ["Брат", "Брат 2"]
    assert blocks["library"] == {
        "status": "ok",
        "data": [
            {"media_item_id": str(ITEM), "shelves": ["system:watched"]},
            {"media_item_id": str(ITEM_2), "shelves": []},
        ],
    }
    assert result.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert shelves_1.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert shelves_1.call_count == 1


async def test_match_page_requires_user_without_calling_match(bff: Bff) -> None:
    result, _, _ = _match_routes(bff)
    path = f"/v1/pages/match/{MATCH_ID}"

    assert_problem(await bff.client.get(path), 401, "auth.unauthenticated")
    assert_problem(
        await bff.client.get(path, headers=bff.bearer(bff.issuer.issue_guest())),
        403,
        "auth.guest_forbidden",
    )
    assert result.call_count == 0


async def test_match_page_missing_or_running_is_404(bff: Bff) -> None:
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        404, json={"code": "match.match_not_found", "title": "Not found", "status": 404}
    )
    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    assert_problem(response, 404, "match.match_not_found")


@pytest.mark.parametrize("failure", ["501", "timeout"])
async def test_match_page_match_down_is_200_unavailable(bff: Bff, failure: str) -> None:
    route = bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}")
    if failure == "501":
        route.respond(501, json={"code": "core.not_implemented", "status": 501})
    else:
        route.mock(side_effect=httpx.ConnectTimeout("slow"))

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    assert response.json() == {
        "blocks": {"match": UNAVAILABLE, "items": UNAVAILABLE, "library": UNAVAILABLE}
    }


async def test_match_page_catalog_and_library_degrade_independently(bff: Bff) -> None:
    _, item_2, _ = _match_routes(bff)
    item_2.respond(503)

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["match"]["status"] == "ok"
    assert blocks["items"] == UNAVAILABLE
    assert blocks["library"]["status"] == "ok"


async def test_match_page_library_timeout_hides_only_flags(bff: Bff) -> None:
    _match_routes(bff)
    bff.stubs.library.route("GET", f"/v1/items/{ITEM_2}/shelves").mock(
        side_effect=httpx.ReadTimeout("slow")
    )

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    blocks = response.json()["blocks"]
    assert blocks["library"] == UNAVAILABLE
    assert blocks["items"]["status"] == "ok"


async def test_match_page_blocked_item_is_skipped(bff: Bff) -> None:
    _, item_2, _ = _match_routes(bff)
    item_2.respond(404, json={"code": "catalog.item_not_found", "title": "Nf", "status": 404})

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    blocks = response.json()["blocks"]
    assert [i["media_item_id"] for i in blocks["items"]["data"]] == [str(ITEM)]
    assert blocks["library"]["status"] == "ok"


async def test_match_page_id_must_be_uuid(bff: Bff) -> None:
    response = await bff.client.get("/v1/pages/match/123", headers=bff.bearer(bff.issuer.issue()))
    assert response.status_code == 422


# ----------------------------------------------------------------------------- ревью 4a
async def test_online_page_expired_token_is_session_expired_without_calls(bff: Bff) -> None:
    from datetime import timedelta

    rooms, matches, modes = _online_routes(bff)
    expired = bff.issuer.issue(ttl=timedelta(seconds=-120))

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(expired))

    assert response.status_code == 200
    assert response.headers["cache-control"] == PRIVATE
    assert response.json() == {"status": "session_expired", "blocks": None}
    assert rooms.call_count == matches.call_count == modes.call_count == 0


async def test_online_page_match_401_is_session_expired(bff: Bff) -> None:
    rooms, _, _ = _online_routes(bff)
    rooms.respond(401, json={"code": "auth.session_revoked", "title": "Unauth", "status": 401})

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.json() == {"status": "session_expired", "blocks": None}


async def test_online_page_match_429_is_unavailable_block(bff: Bff) -> None:
    _, matches, _ = _online_routes(bff)
    matches.respond(429, json={"code": "core.rate_limited", "title": "Too many", "status": 429})

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["my_matches"] == UNAVAILABLE
    assert blocks["open_rooms"]["status"] == "ok"


@pytest.mark.parametrize("status", [429, 400, 410])
async def test_match_page_match_429_or_undescribed_4xx_is_unavailable(
    bff: Bff, status: int
) -> None:
    result, _, _ = _match_routes(bff)
    result.respond(status, json={"code": "match.odd", "title": "Odd", "status": status})

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    assert response.json() == {
        "blocks": {"match": UNAVAILABLE, "items": UNAVAILABLE, "library": UNAVAILABLE}
    }


async def test_match_page_foreign_match_id_in_answer_is_contract_violation(bff: Bff) -> None:
    result, _, _ = _match_routes(bff)
    other = {**SAMPLES["match_result"], "match_id": "0192a3b4-c5d6-7e8f-9a0b-000000000dff"}
    result.respond(200, json=other)

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    assert response.json()["blocks"]["match"] == UNAVAILABLE


async def test_match_page_foreign_item_in_shelves_answer_is_contract_violation(bff: Bff) -> None:
    _, _, shelves_1 = _match_routes(bff)
    shelves_1.respond(200, json={"media_item_id": str(ITEM_2), "shelves": ["system:watched"]})

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    blocks = response.json()["blocks"]
    assert blocks["library"] == UNAVAILABLE  # флаг чужой единицы не приписываем нашей
    assert blocks["items"]["status"] == "ok"


async def test_online_modes_without_online_block_are_contract_violation(bff: Bff) -> None:
    """game без поддержки ``session`` отдал бы соло-режимы — блок гаснет, а не путает хаб."""
    _, _, modes = _online_routes(bff)
    modes.respond(200, json=MODES)

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    blocks = response.json()["blocks"]
    assert blocks["modes"] == UNAVAILABLE
    assert blocks["open_rooms"]["status"] == "ok"
