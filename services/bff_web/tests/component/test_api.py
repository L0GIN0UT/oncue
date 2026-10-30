"""bff_web через HTTP: источники — заглушки по их OpenAPI-контрактам, кэш — реальный Redis."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
RATING = "http://rating.test"
PROGRESSION = "http://progression.test"
NOTIFICATIONS = "http://notifications.test"
SOCIAL = "http://social.test"
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


SERVICE_TOKEN = TestTokenIssuer().issue_service("bff_web", "game", ["daily.results"])
REWARDS_PENDING = {
    "session_id": str(SESSION),
    "settled": False,
    "xp": 0,
    "verses": 0,
    "level_before": None,
    "level_after": None,
    "streak": None,
}


@dataclass
class Stubs:
    game: ServiceStub
    catalog: ServiceStub
    identity: ServiceStub
    collections: ServiceStub
    library: ServiceStub
    match: ServiceStub
    rating: ServiceStub
    progression: ServiceStub
    notifications: ServiceStub
    social: ServiceStub


@dataclass
class Bff:
    client: httpx.AsyncClient
    stubs: Stubs
    issuer: TestTokenIssuer
    rating_routes: dict[str, respx.Route] = field(default_factory=dict)

    def bearer(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


RELATION_OPEN = {
    "profile_visible": True,
    "stats_visible": True,
    "is_friend": False,
    "can_send_request": True,
    "blocked_by_me": False,
}


def _social_defaults(social: ServiceStub) -> None:
    """Пустой, но исправный social: тесты 5a-5b не знают о нём, тесты 5c переопределяют ответы."""
    social.route("GET", f"/v1/players/{PLAYER_ID}/relation").respond(200, json=RELATION_OPEN)
    social.route("GET", "/v1/me/friends").respond(200, json={"items": [], "total": 0})
    social.route("GET", "/v1/me/friend-requests").respond(
        200, json={"incoming": [], "outgoing": []}
    )
    social.route("GET", "/v1/me/sanctions").respond(200, json={"items": []})
    social.route("GET", "/v1/players/relations").respond(200, json={"items": []})
    social.route("GET", f"/v1/collections/{COLLECTION_ID}/comments").respond(
        200, json={"items": [], "total": 0, "next_cursor": None}
    )


def _progression_defaults(progression: ServiceStub) -> None:
    """Событий нет: тесты 5a-5c не знают о них, тесты 5d переопределяют ответы."""
    progression.route("GET", "/v1/events/active").respond(200, json={"event": None})
    progression.route("GET", "/v1/me/events").respond(200, json={"items": []})
    progression.route("GET", "/v1/me/rewards").respond(200, json=REWARDS_PENDING)


@pytest.fixture
def stubs() -> Iterator[Stubs]:
    with ExitStack() as stack:
        social = stack.enter_context(stub_service(SOCIAL, service="social"))
        _social_defaults(social)
        progression = stack.enter_context(stub_service(PROGRESSION, service="progression"))
        _progression_defaults(progression)
        identity = stack.enter_context(stub_service(IDENTITY, service="identity"))
        identity.route("POST", "/internal/v1/service-tokens").respond(
            200, json={"access_token": SERVICE_TOKEN, "token_type": "Bearer", "expires_in": 600}
        )
        yield Stubs(
            social=social,
            progression=progression,
            game=stack.enter_context(stub_service(GAME, service="game")),
            catalog=stack.enter_context(stub_service(CATALOG, service="catalog")),
            identity=identity,
            collections=stack.enter_context(stub_service(COLLECTIONS, service="collections")),
            library=stack.enter_context(stub_service(LIBRARY, service="library")),
            match=stack.enter_context(stub_service(MATCH, service="match")),
            rating=stack.enter_context(stub_service(RATING, service="rating")),
            notifications=stack.enter_context(stub_service(NOTIFICATIONS, service="notifications")),
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
            "rating_url": RATING,
            "progression_url": PROGRESSION,
            "notifications_url": NOTIFICATIONS,
            "social_url": SOCIAL,
            "timeout_s": 0.5,
            "breaker_failures": 3,
        },
        page_cache={"key_prefix": prefix},
        pages={"relations_batch_size": 3, "results_rewards_retry_delay_s": 0},
        service_auth={
            "token_url": f"{IDENTITY}/internal/v1/service-tokens",
            "client_secret": "test-service-secret",
        },
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
    _progression_routes(bff)

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert "Cookie" in response.headers["vary"]
    blocks = response.json()["blocks"]
    assert blocks["progress"]["status"] == "ok"
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
            "progress": None,
            "event": None,
            "feed": None,
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
RATING_SAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "data" / "rating.json").read_text(encoding="utf-8")
)
UNAVAILABLE = {"status": "unavailable", "data": None}
EMPTY_SANCTIONS = {"status": "ok", "data": {"items": []}}


def _online_routes(bff: Bff) -> tuple[respx.Route, respx.Route, respx.Route]:
    rooms = bff.stubs.match.route("GET", "/v1/rooms/open").respond(200, json=SAMPLES["open_rooms"])
    matches = bff.stubs.match.route("GET", "/v1/me/matches").respond(
        200, json=SAMPLES["my_matches"]
    )
    modes = bff.stubs.game.route("GET", "/v1/modes").respond(200, json=SAMPLES["online_modes"])
    # 5c, ADR 0069: игроки карточки истории - из итога матча (тесты 4b их не проверяют).
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json=SAMPLES["match_result"]
    )
    _rating_routes(bff)
    return rooms, matches, modes


def _rating_routes(bff: Bff) -> dict[str, respx.Route]:
    """Источники 4b: очередь match и ручки rating (проверяются по OpenAPI-снимкам)."""
    bff.rating_routes = {
        "pool": bff.stubs.match.route("GET", "/v1/queue/pool").respond(
            200, json=RATING_SAMPLES["queue_pool"]
        ),
        "ladders": bff.stubs.rating.route("GET", "/v1/ladders").respond(
            200, json=RATING_SAMPLES["ladders"]
        ),
        "mine": bff.stubs.rating.route("GET", "/v1/me/ratings").respond(
            200, json=RATING_SAMPLES["my_ratings"]
        ),
        "weekly": bff.stubs.rating.route("GET", "/v1/ladders/film/weekly").respond(
            200, json=RATING_SAMPLES["weekly"]
        ),
        "weekly_music": bff.stubs.rating.route("GET", "/v1/ladders/music/weekly").respond(
            200, json={**RATING_SAMPLES["weekly"], "ladder": "music"}
        ),
        "board": bff.stubs.rating.route("GET", "/v1/ladders/film/leaderboard").respond(
            200, json=RATING_SAMPLES["leaderboard"]
        ),
    }
    return bff.rating_routes


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
    cards = [{**m, "participants": []} for m in SAMPLES["my_matches"]["items"]]
    assert blocks["my_matches"] == {
        "status": "ok",
        "data": {**SAMPLES["my_matches"], "items": cards},
    }
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


async def test_online_page_4b_blocks(bff: Bff) -> None:
    token = bff.issuer.issue()
    _online_routes(bff)
    routes = bff.rating_routes

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    blocks = response.json()["blocks"]
    assert blocks["queues"] == {"status": "ok", "data": RATING_SAMPLES["queue_pool"]["items"]}
    film, music = blocks["ratings"]["data"]
    assert (film["rating"], film["tier"]["title_key"], film["place"]) == (
        1234,
        "rating.tier.silver",
        42,
    )
    assert (music["rating"], music["tier"], music["calibrating"], music["calibration_left"]) == (
        None,
        None,
        True,
        7,
    )
    assert blocks["seasons"]["data"] == [
        {
            "ladder": "film",
            "number": 4,
            "starts_at": "2026-10-19T00:00:00Z",
            "ends_at": "2026-12-14T00:00:00Z",
        }
    ]
    weekly = blocks["weekly"]["data"]
    assert [w["ladder"] for w in weekly] == ["film", "music"]
    assert len(weekly[0]["items"]) == 3  # меньше online_weekly_limit
    assert routes["mine"].calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert routes["pool"].calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert routes["weekly"].calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert "authorization" not in routes["ladders"].calls.last.request.headers  # публичный


async def test_online_page_4b_rating_down_hides_rating_blocks_only(bff: Bff) -> None:
    _online_routes(bff)
    routes = bff.rating_routes
    routes["ladders"].respond(503)
    routes["mine"].mock(side_effect=httpx.ReadTimeout("slow"))
    routes["pool"].respond(
        501, json={"code": "core.not_implemented", "title": "Not implemented", "status": 501}
    )

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    for name in ("queues", "ratings", "seasons", "weekly"):
        assert blocks[name] == UNAVAILABLE, name
    assert blocks["open_rooms"]["status"] == blocks["modes"]["status"] == "ok"


async def test_online_page_4b_rating_401_is_session_expired(bff: Bff) -> None:
    _online_routes(bff)
    bff.rating_routes["mine"].respond(
        401, json={"code": "auth.session_revoked", "title": "Unauth", "status": 401}
    )

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.json() == {"status": "session_expired", "blocks": None}


async def test_online_page_ladders_are_cached_but_personal_blocks_are_not(bff: Bff) -> None:
    _online_routes(bff)
    routes = bff.rating_routes
    headers = bff.bearer(bff.issuer.issue())

    await bff.client.get("/v1/pages/online", headers=headers)
    await bff.client.get("/v1/pages/online", headers=headers)

    assert routes["ladders"].call_count == 1  # общий, 10 с
    assert routes["mine"].call_count == routes["weekly"].call_count == 2  # личные
    assert routes["pool"].call_count == 2


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
    bff.stubs.match.route("GET", "/v1/queue/pool").respond(501, json=problem)
    bff.stubs.rating.route("GET", "/v1/ladders").respond(501, json=problem)
    bff.stubs.rating.route("GET", "/v1/me/ratings").respond(501, json=problem)

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "blocks": {
            "open_rooms": UNAVAILABLE,
            "my_matches": UNAVAILABLE,
            "modes": UNAVAILABLE,
            "queues": UNAVAILABLE,
            "ratings": UNAVAILABLE,
            "seasons": UNAVAILABLE,
            "weekly": UNAVAILABLE,
            "friends": {"status": "ok", "data": {"items": [], "incoming_requests": 0}},
        },
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
def _items_batch_route(bff: Bff) -> respx.Route:
    """``GET /v1/items?ids=``: карточка на каждый запрошенный id, в обратном порядке."""

    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("ids")
        cards = [{**CARD, "id": i, "title": MATCH_TITLES.get(i, "Брат")} for i in reversed(ids)]
        return httpx.Response(200, json={"items": cards})

    return bff.stubs.catalog.route("GET", "/v1/items").mock(side_effect=answer)


def _shelves_batch_route(bff: Bff) -> respx.Route:
    """``GET /v1/items/shelves?ids=``: запись на каждый id, ``ITEM`` лежит на «просмотрено»."""

    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("ids")
        items = [
            {"media_item_id": i, "shelves": ["system:watched"] if i == str(ITEM) else []}
            for i in ids
        ]
        return httpx.Response(200, json={"items": items})

    return bff.stubs.library.route("GET", "/v1/items/shelves").mock(side_effect=answer)


MATCH_TITLES = {str(ITEM_2): "Брат 2"}


def _match_routes(bff: Bff) -> tuple[respx.Route, respx.Route, respx.Route]:
    result = bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json=SAMPLES["match_result"]
    )
    items = _items_batch_route(bff)
    shelves = _shelves_batch_route(bff)
    _appearance_route(bff)
    return result, items, shelves


async def test_match_page_result_items_and_library_flags(bff: Bff) -> None:
    token = bff.issuer.issue()
    result, items, shelves = _match_routes(bff)

    response = await bff.client.get(f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    expected = SAMPLES["match_result"]
    # 5c: `can_report` добавляет bff (в 0a - всегда false, решение в потоке F5).
    players = [{**p, "can_report": False, "can_add_friend": False} for p in expected["players"]]
    assert blocks["match"] == {"status": "ok", "data": {**expected, "players": players}}
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
    assert shelves.calls.last.request.headers["authorization"] == f"Bearer {token}"
    # N+1 нет: по одному пакетному вызову на источник, дубли единиц схлопнуты.
    assert items.call_count == shelves.call_count == 1
    assert sorted(items.calls.last.request.url.params.get_list("ids")) == [str(ITEM), str(ITEM_2)]
    assert "authorization" not in items.calls.last.request.headers  # карточки публичные


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
        "blocks": {
            "match": UNAVAILABLE,
            "items": UNAVAILABLE,
            "library": UNAVAILABLE,
            "appearance": UNAVAILABLE,
        }
    }


async def test_match_page_catalog_and_library_degrade_independently(bff: Bff) -> None:
    _, items, _ = _match_routes(bff)
    items.mock(return_value=httpx.Response(503))

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["match"]["status"] == "ok"
    assert blocks["items"] == UNAVAILABLE
    assert blocks["library"]["status"] == "ok"


async def test_match_page_library_timeout_hides_only_flags(bff: Bff) -> None:
    _, _, shelves = _match_routes(bff)
    shelves.mock(side_effect=httpx.ReadTimeout("slow"))

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    blocks = response.json()["blocks"]
    assert blocks["library"] == UNAVAILABLE
    assert blocks["items"]["status"] == "ok"


async def test_match_page_blocked_item_is_skipped(bff: Bff) -> None:
    _, items, _ = _match_routes(bff)
    items.mock(return_value=httpx.Response(200, json={"items": [{**CARD, "id": str(ITEM)}]}))

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
        "blocks": {
            "match": UNAVAILABLE,
            "items": UNAVAILABLE,
            "library": UNAVAILABLE,
            "appearance": UNAVAILABLE,
        }
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
    _, _, shelves = _match_routes(bff)
    foreign = {
        "media_item_id": "0192a3b4-c5d6-7e8f-9a0b-0000000000ff",
        "shelves": ["system:watched"],
    }
    shelves.mock(return_value=httpx.Response(200, json={"items": [foreign]}))

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


LB_PUBLIC = "public, max-age=10, stale-while-revalidate=60"


async def test_leaderboards_page_is_public_and_cached_10s(bff: Bff) -> None:
    routes = _rating_routes(bff)

    first = await bff.client.get("/v1/pages/leaderboards", params={"ladder": "film"})
    second = await bff.client.get("/v1/pages/leaderboards", params={"ladder": "film"})

    assert first.status_code == second.status_code == 200, first.text
    assert first.headers["cache-control"] == LB_PUBLIC
    assert first.headers["vary"] == "Authorization, Cookie"
    blocks = first.json()["blocks"]
    assert [x["key"] for x in blocks["ladders"]["data"]] == ["film", "music"]
    film = blocks["ladders"]["data"][0]
    assert film["season"]["number"] == 4 and film["tiers"][0]["key"] == "iron_1"
    board = blocks["leaderboard"]["data"]
    assert board["next_cursor"] == "c2" and board["items"][0]["nickname"] == "neo"
    assert board["items"][0]["tier"]["title_key"] == "rating.tier.cinephile"
    assert blocks["weekly"]["data"]["items"][0]["delta"] == 55
    # Общие данные: токен в rating не уходит, повтор — из Redis.
    for name in ("ladders", "board", "weekly"):
        assert routes[name].call_count == 1, name
        assert "authorization" not in routes[name].calls.last.request.headers


async def test_leaderboards_page_defaults_to_first_ladder_and_passes_cursor(bff: Bff) -> None:
    routes = _rating_routes(bff)

    response = await bff.client.get("/v1/pages/leaderboards", params={"cursor": "c1"})

    assert response.status_code == 200, response.text
    assert dict(routes["board"].calls.last.request.url.params) == {"cursor": "c1"}
    assert routes["weekly"].call_count == 1


async def test_leaderboards_page_around_me_is_personal(bff: Bff) -> None:
    routes = _rating_routes(bff)
    token = bff.issuer.issue()
    me = {**RATING_SAMPLES["leaderboard"]["items"][0], "place": 42, "nickname": "me"}
    routes["board"].respond(200, json={**RATING_SAMPLES["leaderboard"], "me": me})

    response = await bff.client.get(
        "/v1/pages/leaderboards",
        params={"ladder": "film", "around": "me"},
        headers=bff.bearer(token),
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert response.json()["blocks"]["leaderboard"]["data"]["me"]["place"] == 42
    request = routes["board"].calls.last.request
    assert request.headers["authorization"] == f"Bearer {token}"
    assert dict(request.url.params) == {"around": "me"}
    assert routes["weekly"].calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_leaderboards_page_around_me_without_session_is_401(bff: Bff) -> None:
    routes = _rating_routes(bff)

    response = await bff.client.get("/v1/pages/leaderboards", params={"around": "me"})

    assert_problem(response, 401, "auth.unauthenticated")
    assert routes["board"].call_count == 0


async def test_leaderboards_page_around_me_in_calibration_returns_first_page(bff: Bff) -> None:
    routes = _rating_routes(bff)
    not_ranked = {"code": "rating.not_ranked", "title": "Not ranked", "status": 404}

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("around"):
            return httpx.Response(404, json=not_ranked)
        return httpx.Response(200, json=RATING_SAMPLES["leaderboard"])

    routes["board"].mock(side_effect=answer)

    response = await bff.client.get(
        "/v1/pages/leaderboards",
        params={"ladder": "film", "around": "me"},
        headers=bff.bearer(bff.issuer.issue()),
    )

    assert response.status_code == 200, response.text
    assert response.json()["blocks"]["leaderboard"]["data"]["items"][0]["place"] == 1
    assert routes["board"].call_count == 2


async def test_leaderboards_page_ignores_guest_token(bff: Bff) -> None:
    routes = _rating_routes(bff)

    response = await bff.client.get(
        "/v1/pages/leaderboards",
        params={"ladder": "film"},
        headers=bff.bearer(bff.issuer.issue_guest()),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == LB_PUBLIC
    assert "authorization" not in routes["board"].calls.last.request.headers


async def test_leaderboards_page_unknown_ladder_is_404(bff: Bff) -> None:
    _rating_routes(bff)
    missing = {"code": "rating.ladder_not_found", "title": "Not found", "status": 404}
    for path in ("leaderboard", "weekly"):
        bff.stubs.rating.route("GET", f"/v1/ladders/nope/{path}").respond(404, json=missing)

    response = await bff.client.get("/v1/pages/leaderboards", params={"ladder": "nope"})

    assert_problem(response, 404, "rating.ladder_not_found")


async def test_leaderboards_page_foreign_cursor_is_422(bff: Bff) -> None:
    routes = _rating_routes(bff)
    routes["board"].respond(
        422, json={"code": "core.invalid_input", "title": "Bad cursor", "status": 422}
    )

    response = await bff.client.get(
        "/v1/pages/leaderboards", params={"ladder": "film", "cursor": "zzz"}
    )

    assert_problem(response, 422, "core.invalid_input")


async def test_leaderboards_page_rating_down_degrades_blocks_with_200(bff: Bff) -> None:
    routes = _rating_routes(bff)
    routes["ladders"].respond(503)
    routes["board"].mock(side_effect=httpx.ReadTimeout("slow"))
    routes["weekly"].respond(200, json={"junk": True})  # ответ вне контракта

    response = await bff.client.get("/v1/pages/leaderboards", params={"ladder": "film"})

    assert response.status_code == 200
    assert response.json() == {
        "blocks": {"ladders": UNAVAILABLE, "leaderboard": UNAVAILABLE, "weekly": UNAVAILABLE}
    }


async def test_leaderboards_page_validates_query(bff: Bff) -> None:
    for params in ({"ladder": "Film!"}, {"around": "all"}, {"cursor": "bad cursor"}):
        response = await bff.client.get("/v1/pages/leaderboards", params=params)
        assert response.status_code == 422, params


# ----------------------------------------------------------------------------- прогрессия (5a)
PROG_PROGRESS = {
    "level": 7,
    "max_level": 100,
    "total_xp": 3700,
    "xp_in_level": 120,
    "xp_to_next": 380,
    "solo_xp_today": 300,
    "solo_xp_cap": 2000,
    "streaks": [
        {"domain": "music", "current": 3, "best": 9, "last_day": "2026-10-22"},
        {"domain": "film", "current": 5, "best": 5, "last_day": "2026-10-22"},
    ],
    "boost": {"multiplier": 1.5, "matches_left": 2},
    "boosts_queued": 0,
    "config_version": 1,
}
PROG_WALLET = {
    "currency": "verses",
    "earned": 40,
    "paid": 0,
    "total": 40,
    "recent": [
        {
            "op_id": "o1",
            "direction": "credit",
            "amount": 5,
            "bucket": "earned",
            "reason": "daily",
            "at": "2026-10-22T10:00:00Z",
        }
    ],
}
_REWARD = {"xp": 50, "verses": 5}
PROG_QUESTS = {
    "daily": {
        "period": "daily",
        "period_start": "2026-10-23",
        "period_end": "2026-10-23",
        "items": [
            {
                "key": "play_three",
                "title": "Сыграй три раунда",
                "description": "Любой режим",
                "target": 3,
                "progress": 3,
                "completed": True,
                "reward": _REWARD,
            },
            {
                "key": "win_duel",
                "title": "Выиграй дуэль",
                "description": "Онлайн",
                "target": 1,
                "progress": 0,
                "completed": False,
                "reward": _REWARD,
            },
        ],
    },
    "weekly": {
        "period": "weekly",
        "period_start": "2026-10-19",
        "period_end": "2026-10-25",
        "items": [
            {
                "key": "streak_week",
                "title": "Неделя дейликов",
                "description": "7 дней",
                "target": 7,
                "progress": 2,
                "completed": False,
                "reward": {"xp": 300, "verses": 20},
            }
        ],
    },
    "one_time": {
        "period": "one_time",
        "period_start": "2026-10-01",
        "period_end": None,
        "items": [
            {
                "key": "first_collection",
                "title": "Первый сборник",
                "description": "Опубликуй сборник",
                "target": 1,
                "progress": 0,
                "completed": False,
                "reward": _REWARD,
            }
        ],
    },
}
PROG_BADGES = {
    "items": [
        {
            "key": "first_win",
            "title": "Первая победа",
            "description": "Выиграй дуэль",
            "target": 1,
            "progress": 1,
            "unlocked_at": "2026-10-20T12:00:00Z",
        },
        {
            "key": "streak_7",
            "title": "Неделя подряд",
            "description": "Серия 7",
            "target": 7,
            "progress": 3,
            "unlocked_at": None,
        },
    ]
}
_PRIZE_NONE = {"kind": "none", "amount": 0, "multiplier": None, "matches": None, "item_key": None}
PROG_WHEEL = {
    "enabled": True,
    "day": "2026-10-23",
    "today": None,
    "next_spin_at": None,
    "config_version": 1,
    "sectors": [
        {
            "key": "verses_5",
            "title": "5 Версов",
            "weight": 30,
            "chance": 0.3,
            "prize": {**_PRIZE_NONE, "kind": "verses", "amount": 5},
        },
        {"key": "empty", "title": "Пусто", "weight": 70, "chance": 0.7, "prize": _PRIZE_NONE},
    ],
}
PROG_SPUN = {
    **PROG_WHEEL,
    "today": {
        "day": "2026-10-23",
        "sector_key": "empty",
        "prize": _PRIZE_NONE,
        "spun_at": "2026-10-23T08:00:00Z",
        "repeated": False,
    },
    "next_spin_at": "2026-10-24T00:00:00Z",
}


def cosmetic(n: int, slot: str, **extra: Any) -> dict[str, Any]:
    """Предмет косметики по снимку progression (``ItemView``)."""
    return {
        "id": str(UUID(int=n)),
        "key": f"item_{n}",
        "slot": slot,
        "rarity": "common",
        "title": f"Предмет {n}",
        "media_kind": "static",
        "asset_url": f"/media/cosmetics/{n}.webp",
        "poster_url": None,
        "level_required": None,
        "price": 100,
        "event_key": None,
        **extra,
    }


PROG_APPEARANCE = {
    "slots": [
        {"slot": "frame", "item": cosmetic(1, "frame")},
        {"slot": "title", "item": None},
    ],
    "custom_avatar_url": "/media/avatars/me.webp",
    "custom_avatar_asset_id": str(UUID(int=77)),
}
PROG_INVENTORY = {
    "items": [
        {
            "item": cosmetic(1, "frame"),
            "source": "purchase",
            "acquired_at": "2026-10-20T10:00:00Z",
            "expires_at": None,
            "equipped": True,
        }
    ]
}
PROG_AVATAR = {
    "entitled": True,
    "current_url": "/media/avatars/deprecated.webp",  # устарело: bff его не читает
    "request": {
        "asset_id": str(UUID(int=77)),
        "status": "approved",
        "submitted_at": "2026-10-21T10:00:00Z",
        "reason_code": None,
    },
    "limits": {
        "max_bytes": 262144,
        "formats": ["webp", "png"],
        "min_side_px": 64,
        "max_side_px": 512,
    },
}


def _progression_routes(bff: Bff, *, wheel: dict[str, Any] = PROG_WHEEL) -> dict[str, respx.Route]:
    stub = bff.stubs.progression
    return {
        "progress": stub.route("GET", "/v1/me/progress").respond(200, json=PROG_PROGRESS),
        "wallet": stub.route("GET", "/v1/me/wallet").respond(200, json=PROG_WALLET),
        "quests": stub.route("GET", "/v1/me/quests").respond(200, json=PROG_QUESTS),
        "badges": stub.route("GET", "/v1/me/badges").respond(200, json=PROG_BADGES),
        "wheel": stub.route("GET", "/v1/wheel").respond(200, json=wheel),
        "appearance": stub.route("GET", "/v1/me/appearance").respond(200, json=PROG_APPEARANCE),
        "inventory": stub.route("GET", "/v1/me/inventory").respond(200, json=PROG_INVENTORY),
        "avatar": stub.route("GET", "/v1/me/avatar").respond(200, json=PROG_AVATAR),
    }


async def test_profile_page_all_blocks_ok(bff: Bff) -> None:
    token = bff.issuer.issue()
    routes = _progression_routes(bff)

    response = await bff.client.get("/v1/pages/profile", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    assert set(blocks) == {
        "progress",
        "wallet",
        "quests",
        "badges",
        "wheel",
        "appearance",
        "inventory",
        "avatar",
        "events",
    }
    progress = blocks["progress"]["data"]
    assert (progress["level"], progress["xp_in_level"], progress["xp_to_next"]) == (7, 120, 380)
    assert progress["boost"] == {"multiplier": 1.5, "matches_left": 2}
    assert [(s["domain"], s["current"], s["best"]) for s in progress["streaks"]] == [
        ("music", 3, 9),
        ("film", 5, 5),
    ]
    assert (progress["solo_xp_today"], progress["solo_xp_cap"]) == (300, 2000)
    assert blocks["wallet"]["data"] == {"earned": 40, "paid": 0, "total": 40}
    daily = blocks["quests"]["data"]["daily"]
    assert [q["title"] for q in daily] == ["Сыграй три раунда", "Выиграй дуэль"]
    assert daily[0]["reward_xp"] == 50 and daily[0]["completed"] is True
    assert blocks["quests"]["data"]["weekly"][0]["reward_verses"] == 20
    assert [b["key"] for b in blocks["badges"]["data"]] == ["first_win", "streak_7"]
    assert blocks["badges"]["data"][1]["unlocked_at"] is None
    wheel = blocks["wheel"]["data"]
    assert (wheel["enabled"], wheel["can_spin"], wheel["next_spin_at"]) == (True, True, None)
    assert [(s["key"], s["title"], s["chance"]) for s in wheel["sectors"]] == [
        ("verses_5", "5 Версов", 0.3),
        ("empty", "Пусто", 0.7),
    ]
    assert wheel["today"] is None
    appearance = blocks["appearance"]["data"]
    assert appearance["custom_avatar_url"] == "/media/avatars/me.webp"  # из /v1/me/appearance
    assert appearance["custom_avatar_asset_id"] == str(UUID(int=77))
    assert "deprecated" not in str(blocks["avatar"])
    assert [(s["slot"], s["item"] and s["item"]["key"]) for s in appearance["slots"]] == [
        ("frame", "item_1"),
        ("title", None),
    ]
    assert "event_key" not in appearance["slots"][0]["item"]  # поле 5d фронту не нужно
    inventory = blocks["inventory"]["data"]["items"]
    assert [(e["item"]["key"], e["source"], e["equipped"]) for e in inventory] == [
        ("item_1", "purchase", True)
    ]
    assert blocks["avatar"]["data"] == {
        "entitled": True,
        "request": {
            "status": "approved",
            "submitted_at": "2026-10-21T10:00:00Z",
            "reason_code": None,
        },
        "max_bytes": 262144,
        "formats": ["webp", "png"],
    }
    for route in routes.values():
        assert route.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_profile_page_wheel_already_spun(bff: Bff) -> None:
    _progression_routes(bff, wheel=PROG_SPUN)

    response = await bff.client.get("/v1/pages/profile", headers=bff.bearer(bff.issuer.issue()))

    wheel = response.json()["blocks"]["wheel"]["data"]
    assert wheel["can_spin"] is False
    assert wheel["next_spin_at"] == "2026-10-24T00:00:00Z"
    assert wheel["today"]["sector_key"] == "empty"


async def test_profile_page_anonymous_and_guest_do_not_call_sources(bff: Bff) -> None:
    routes = _progression_routes(bff)
    guest = bff.bearer(bff.issuer.issue_guest())

    assert_problem(await bff.client.get("/v1/pages/profile"), 401, "auth.unauthenticated")
    assert_problem(
        await bff.client.get("/v1/pages/profile", headers=guest), 403, "auth.guest_forbidden"
    )
    assert all(route.call_count == 0 for route in routes.values())


@pytest.mark.parametrize(
    ("block", "route_name"),
    [
        ("progress", "progress"),
        ("wallet", "wallet"),
        ("quests", "quests"),
        ("badges", "badges"),
        ("wheel", "wheel"),
        ("appearance", "appearance"),
        ("inventory", "inventory"),
        ("avatar", "avatar"),
    ],
)
@pytest.mark.parametrize("failure", ["503", "501", "timeout", "garbage"])
async def test_profile_page_each_block_degrades_independently(
    bff: Bff, block: str, route_name: str, failure: str
) -> None:
    routes = _progression_routes(bff)
    route = routes[route_name]
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "garbage":
        route.respond(200, json={"nope": 1})
    else:
        route.respond(int(failure), json={"code": "core.not_implemented", "status": int(failure)})

    response = await bff.client.get("/v1/pages/profile", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    # Адрес своей аватарки приходит с `/me/appearance`: блоки оформления и аватара независимы.
    dead = {block}
    assert blocks.pop("events") == []
    for name, value in blocks.items():
        assert value == UNAVAILABLE if name in dead else value["status"] == "ok", name


async def test_profile_page_source_401_is_screen_401(bff: Bff) -> None:
    routes = _progression_routes(bff)
    routes["progress"].respond(
        401, json={"code": "auth.unauthenticated", "title": "x", "status": 401}
    )

    response = await bff.client.get("/v1/pages/profile", headers=bff.bearer(bff.issuer.issue()))

    assert_problem(response, 401, "auth.unauthenticated")


async def test_profile_page_progression_down_is_200_all_unavailable(bff: Bff) -> None:
    bff.stubs.progression.route("GET", "/v1/me/progress").respond(503)
    for path in (
        "/v1/me/wallet",
        "/v1/me/quests",
        "/v1/me/badges",
        "/v1/wheel",
        "/v1/me/appearance",
        "/v1/me/inventory",
        "/v1/me/avatar",
    ):
        bff.stubs.progression.route("GET", path).mock(side_effect=httpx.ConnectError("refused"))

    response = await bff.client.get("/v1/pages/profile", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    assert response.json() == {
        "blocks": {**dict.fromkeys(PROFILE_BLOCKS, UNAVAILABLE), "events": []}
    }


PROFILE_BLOCKS = (
    "progress",
    "wallet",
    "quests",
    "badges",
    "wheel",
    "appearance",
    "inventory",
    "avatar",
)


def _home_game(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=[])


async def test_home_progress_block_for_user(bff: Bff) -> None:
    _home_game(bff)
    routes = _progression_routes(bff)
    token = bff.issuer.issue()

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert response.json()["blocks"]["progress"] == {
        "status": "ok",
        "data": {
            "level": 7,
            "xp_in_level": 120,
            "xp_to_next": 380,
            "best_streak": 5,
            "can_spin": True,
            "quests_open": 1,
        },
    }
    assert routes["wallet"].call_count == 0  # главной кошелёк и бейджи не нужны
    assert routes["badges"].call_count == 0


async def test_home_progress_can_spin_false_when_spun(bff: Bff) -> None:
    _home_game(bff)
    _progression_routes(bff, wheel=PROG_SPUN)

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue()))

    assert response.json()["blocks"]["progress"]["data"]["can_spin"] is False


async def test_home_progress_wheel_disabled_means_cannot_spin(bff: Bff) -> None:
    _home_game(bff)
    _progression_routes(bff, wheel={**PROG_WHEEL, "enabled": False})

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue()))

    assert response.json()["blocks"]["progress"]["data"]["can_spin"] is False


@pytest.mark.parametrize("source", ["progress", "quests", "wheel"])
@pytest.mark.parametrize("failure", ["503", "timeout"])
async def test_home_progress_any_source_failure_hides_only_block(
    bff: Bff, source: str, failure: str
) -> None:
    _home_game(bff)
    route = _progression_routes(bff)[source]
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    else:
        route.respond(503)

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["progress"] == UNAVAILABLE
    assert blocks["modes"]["status"] == "ok"
    assert blocks["dailies"]["status"] == "ok"


async def test_home_progress_is_null_for_guest_and_anonymous_without_calls(bff: Bff) -> None:
    _home_game(bff)
    routes = _progression_routes(bff)

    anonymous = await bff.client.get("/v1/pages/home")
    guest = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue_guest()))

    assert anonymous.json()["blocks"]["progress"] is None
    assert guest.json()["blocks"]["progress"] is None
    assert all(route.call_count == 0 for route in routes.values())


async def test_home_cache_headers_by_viewer(bff: Bff) -> None:
    """Аноним — публично; гость (личные дейлики) и пользователь с блоком progress — private."""
    _home_game(bff)
    _progression_routes(bff)

    anonymous = await bff.client.get("/v1/pages/home")
    guest = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue_guest()))
    user = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue()))

    assert anonymous.headers["cache-control"] == PUBLIC
    assert guest.headers["cache-control"] == PRIVATE
    assert user.headers["cache-control"] == PRIVATE
    assert user.json()["blocks"]["progress"]["status"] == "ok"


async def test_home_rejected_token_does_not_reach_progression(bff: Bff) -> None:
    _home_game(bff)
    routes = _progression_routes(bff)

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer("not-a-jwt"))

    assert response.json()["blocks"]["progress"] is None
    assert all(route.call_count == 0 for route in routes.values())


async def test_profile_mixed_401_403_follows_block_order(bff: Bff) -> None:
    """Первым по порядку блоков (progress, wallet, quests, badges, wheel) решает ответ."""
    routes = _progression_routes(bff)
    routes["wheel"].respond(401, json={"code": "auth.unauthenticated", "status": 401})
    routes["progress"].respond(403, json={"code": "auth.forbidden", "status": 403})

    response = await bff.client.get("/v1/pages/profile", headers=bff.bearer(bff.issuer.issue()))

    assert_problem(response, 403, "auth.forbidden")


PLAYER_ID = "0192a3b4-c5d6-7e8f-9a0b-000000000901"
PLAYER = {
    "user_id": PLAYER_ID,
    "level": 5,
    "total_xp": 2600,
    "badges": [
        {"key": "first_win", "title": "Первая победа", "unlocked_at": "2026-10-20T12:00:00Z"}
    ],
}


async def test_player_page_ok(bff: Bff) -> None:
    token = bff.issuer.issue()
    route = bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress").respond(
        200, json=PLAYER
    )
    looks = _appearance_route(bff)

    response = await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    assert blocks["progress"] == {"status": "ok", "data": PLAYER}
    assert blocks["appearance"]["status"] == "ok"
    assert blocks["appearance"]["data"]["custom_avatar_url"] == "/media/avatars/p.webp"
    assert blocks["appearance"]["data"]["slots"][0]["item"]["key"] == "item_1"
    assert route.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert looks.calls.last.request.url.params.get_list("user_ids") == [PLAYER_ID]


async def test_player_page_anonymous_and_guest_do_not_call_sources(bff: Bff) -> None:
    route = bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress").respond(
        200, json=PLAYER
    )
    looks = _appearance_route(bff)
    guest = bff.bearer(bff.issuer.issue_guest())

    assert_problem(
        await bff.client.get(f"/v1/pages/players/{PLAYER_ID}"), 401, "auth.unauthenticated"
    )
    assert_problem(
        await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=guest),
        403,
        "auth.guest_forbidden",
    )
    assert route.call_count == 0 and looks.call_count == 0


async def test_player_page_unknown_player_is_404_and_down_is_unavailable(bff: Bff) -> None:
    route = bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress")
    headers = bff.bearer(bff.issuer.issue())

    _appearance_route(bff)
    route.respond(404, json={"code": "progression.user_not_found", "status": 404})
    assert_problem(
        await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=headers),
        404,
        "progression.user_not_found",
    )

    route.respond(503)
    response = await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=headers)
    assert response.status_code == 200
    assert response.json()["blocks"]["progress"] == UNAVAILABLE
    assert response.json()["blocks"]["appearance"]["status"] == "ok"


async def test_player_page_appearance_degrades_independently(bff: Bff) -> None:
    headers = bff.bearer(bff.issuer.issue())
    bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress").respond(200, json=PLAYER)
    looks = _appearance_route(bff)

    for failure in (503, 404):  # 404 оформления - не 404 экрана: игрок есть
        looks.respond(failure, json={"code": "progression.x", "status": failure})
        response = await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=headers)
        assert response.status_code == 200
        blocks = response.json()["blocks"]
        assert blocks["progress"]["status"] == "ok" and blocks["appearance"] == UNAVAILABLE


async def test_player_page_requires_uuid(bff: Bff) -> None:
    response = await bff.client.get("/v1/pages/players/not-a-uuid")
    assert response.status_code == 422


# ----------------------------------------------------------------------------- 5b: оформление
def _appearance_route(bff: Bff) -> respx.Route:
    """``GET /v1/appearance?user_ids=``: отвечает по каждому запрошенному id (как источник)."""

    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("user_ids")
        players = [
            {
                "user_id": i,
                "appearance": PROG_APPEARANCE,
                "custom_avatar_url": "/media/avatars/p.webp",
            }
            for i in ids
        ]
        return httpx.Response(200, json={"players": players})

    return bff.stubs.progression.route("GET", "/v1/appearance").mock(side_effect=answer)


async def test_match_page_appearance_batches_by_12_and_dedups(bff: Bff) -> None:
    """14 игроков (+ дубль) - два вызова progression (12 и 2), порядок игроков сохранён."""
    template = SAMPLES["match_result"]
    players = [
        {**template["players"][0], "user_id": str(UUID(int=n)), "nickname": f"p{n}", "slot": n}
        for n in range(1, 15)
    ]
    players.append(players[2])
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json={**template, "players": players}
    )
    looks = _appearance_route(bff)
    _items_batch_route(bff)
    _shelves_batch_route(bff)

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200, response.text
    sizes = sorted(len(c.request.url.params.get_list("user_ids")) for c in looks.calls)
    assert sizes == [2, 12]
    cards = response.json()["blocks"]["appearance"]["data"]
    assert [c["user_id"] for c in cards] == [str(UUID(int=n)) for n in range(1, 15)]
    assert cards[0]["appearance"]["custom_avatar_url"] == "/media/avatars/p.webp"


@pytest.mark.parametrize("failure", ["503", "timeout", "garbage"])
async def test_match_page_appearance_failure_hides_only_its_block(bff: Bff, failure: str) -> None:
    _match_routes(bff)
    route = bff.stubs.progression.route("GET", "/v1/appearance")
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "garbage":
        route.respond(200, json={"nope": 1})
    else:
        route.respond(503)

    response = await bff.client.get(
        f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["appearance"] == UNAVAILABLE
    assert blocks["match"]["status"] == blocks["items"]["status"] == "ok"
    assert blocks["library"]["status"] == "ok"


# ----------------------------------------------------------------------------- 5b: магазин
SHOP_ITEMS = {
    "items": [
        {"item": cosmetic(1, "frame"), "state": "available"},
        {"item": cosmetic(2, "title", level_required=10), "state": "locked"},
        {"item": cosmetic(3, "frame"), "state": "owned"},
        {"item": cosmetic(4, "background", price=None), "state": "owned"},
    ],
    "balance": 250,
}
SHOP_PACKS = {
    "packs": [
        {
            "id": str(UUID(int=500)),
            "key": "starter",
            "title": "Стартовый",
            "style": "classic",
            "items": [cosmetic(1, "frame")],
            "verses_amount": 50,
            "price_kopecks": 19900,
        }
    ]
}


def _shop_routes(bff: Bff) -> dict[str, respx.Route]:
    stub = bff.stubs.progression
    return {
        "items": stub.route("GET", "/v1/shop/items").respond(200, json=SHOP_ITEMS),
        "packs": stub.route("GET", "/v1/shop/packs").respond(200, json=SHOP_PACKS),
    }


async def test_shop_page_catalog_and_packs(bff: Bff) -> None:
    token = bff.issuer.issue()
    routes = _shop_routes(bff)

    response = await bff.client.get("/v1/pages/shop", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    blocks = response.json()["blocks"]
    catalog = blocks["catalog"]["data"]
    assert catalog["balance"] == 250
    assert catalog["slots"] == ["frame", "title", "background"]  # порядок появления в ответе
    assert [(e["item"]["key"], e["state"]) for e in catalog["items"]] == [
        ("item_1", "available"),
        ("item_2", "locked"),
        ("item_3", "owned"),
        ("item_4", "owned"),
    ]
    assert catalog["items"][1]["item"]["level_required"] == 10
    assert catalog["items"][3]["item"]["price"] is None
    pack = blocks["packs"]["data"][0]
    assert (pack["key"], pack["price_kopecks"], pack["items"][0]["key"]) == (
        "starter",
        19900,
        "item_1",
    )
    for route in routes.values():
        assert route.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_shop_page_anonymous_and_guest_do_not_call_sources(bff: Bff) -> None:
    routes = _shop_routes(bff)
    guest = bff.bearer(bff.issuer.issue_guest())

    assert_problem(await bff.client.get("/v1/pages/shop"), 401, "auth.unauthenticated")
    assert_problem(
        await bff.client.get("/v1/pages/shop", headers=guest), 403, "auth.guest_forbidden"
    )
    assert all(route.call_count == 0 for route in routes.values())


async def test_shop_page_billing_disabled_gives_null_packs(bff: Bff) -> None:
    routes = _shop_routes(bff)
    routes["packs"].respond(
        404, json={"code": "progression.billing_disabled", "title": "x", "status": 404}
    )

    response = await bff.client.get("/v1/pages/shop", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["packs"] is None
    assert blocks["catalog"]["status"] == "ok"


async def test_shop_page_blocks_degrade_independently(bff: Bff) -> None:
    routes = _shop_routes(bff)
    headers = bff.bearer(bff.issuer.issue())

    routes["packs"].respond(503)
    blocks = (await bff.client.get("/v1/pages/shop", headers=headers)).json()["blocks"]
    assert blocks["packs"] == UNAVAILABLE and blocks["catalog"]["status"] == "ok"

    routes["packs"].respond(200, json=SHOP_PACKS)
    routes["items"].mock(side_effect=httpx.ReadTimeout("slow"))
    blocks = (await bff.client.get("/v1/pages/shop", headers=headers)).json()["blocks"]
    assert blocks["catalog"] == UNAVAILABLE and blocks["packs"]["status"] == "ok"


async def test_shop_page_source_401_is_screen_401(bff: Bff) -> None:
    routes = _shop_routes(bff)
    routes["items"].respond(401, json={"code": "auth.unauthenticated", "title": "x", "status": 401})

    response = await bff.client.get("/v1/pages/shop", headers=bff.bearer(bff.issuer.issue()))

    assert_problem(response, 401, "auth.unauthenticated")


# ----------------------------------------------------------------------------- 5d: события
ACTIVE_EVENT = {
    "event": {
        "run_id": str(SESSION),
        "key": "winter",
        "title": "Зимняя охота",
        "theme_key": "winter",
        "starts_at": "2026-12-01T00:00:00Z",
        "ends_at": "2026-12-31T00:00:00Z",
    }
}
EVENT_FIND = {
    "key": "snowman",
    "number": 1,
    "title": "Снеговик",
    "x_pct": 12.5,
    "y_pct": 40.0,
    "final": False,
    "state": "found",
    "diary": "Нашёл снеговика",
    "reward": {"xp": 50, "verses": 5},
    "play_page": "solo",
    "task_i18n": "event.winter.snowman.task",
    "trigger": {
        "type": "ui_spot",
        "page_key": "shop",
        "metric": None,
        "progress": None,
        "target": None,
        "period": None,
    },
    "found_at": "2026-12-02T10:00:00Z",
}
EVENT_PAGE = {
    "key": "winter",
    "title": "Зимняя охота",
    "description": "Найди все",
    "theme_key": "winter",
    "status": "active",
    "starts_at": "2026-12-01T00:00:00Z",
    "ends_at": "2026-12-31T00:00:00Z",
    "map_url": "/media/events/winter.webp",
    "found": 1,
    "total": 5,
    "completed": False,
    "reward_item": {"item_key": "frame", "title": "Рамка", "image_url": "/media/frame.webp"},
    "finds": [EVENT_FIND],
}
BANNER_FOUND = {
    "key": "winter",
    "title": "Зимняя охота",
    "ends_at": "2026-12-31T00:00:00Z",
    "found": 1,
    "total": 5,
}
BANNER_ANON = {**BANNER_FOUND, "found": None, "total": None}


def _event_routes(bff: Bff, *, active: dict[str, Any] | None = None) -> None:
    stub = bff.stubs.progression
    stub.route("GET", "/v1/events/active").respond(200, json=active or ACTIVE_EVENT)
    stub.route("GET", "/v1/events/winter").respond(200, json=EVENT_PAGE)


async def test_shell_takes_theme_and_banner_from_active_event(bff: Bff) -> None:
    _event_routes(bff)
    _unread_route(bff)
    token = bff.issuer.issue()

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(token))

    body = response.json()
    assert body["theme"] == {"key": "winter"}
    assert body["event"] == {
        "key": "winter",
        "title": "Зимняя охота",
        "ends_at": "2026-12-31T00:00:00Z",
        "found": 1,
        "total": 5,
    }


async def test_shell_anonymous_gets_banner_without_progress(bff: Bff) -> None:
    _event_routes(bff)

    response = await bff.client.get("/v1/pages/shell")

    assert response.headers["cache-control"] == PUBLIC
    assert response.json()["theme"] == {"key": "winter"}
    assert response.json()["event"] == {**BANNER_ANON, "ends_at": "2026-12-31T00:00:00Z"}


async def test_shell_active_event_is_cached_between_requests(bff: Bff) -> None:
    _event_routes(bff)

    await bff.client.get("/v1/pages/shell")
    await bff.client.get("/v1/pages/shell")

    assert bff.stubs.progression.route("GET", "/v1/events/active").call_count == 1


async def test_shell_event_progress_is_cached_per_player(bff: Bff) -> None:
    _event_routes(bff)
    _unread_route(bff)
    first, second = uuid4(), uuid4()

    for user in (first, first, second):
        response = await bff.client.get(
            "/v1/pages/shell", headers=bff.bearer(bff.issuer.issue(user_id=user))
        )
        assert response.json()["event"]["found"] == 1

    page_route = bff.stubs.progression.route("GET", "/v1/events/winter")
    assert page_route.call_count == 2  # один вызов на игрока, а не на запрос


async def test_event_page_screen_is_not_served_from_the_shell_cache(bff: Bff) -> None:
    _event_routes(bff)
    _unread_route(bff)
    headers = bff.bearer(bff.issuer.issue(user_id=uuid4()))

    await bff.client.get("/v1/pages/shell", headers=headers)
    await bff.client.get("/v1/pages/events/winter", headers=headers)

    page_route = bff.stubs.progression.route("GET", "/v1/events/winter")
    assert page_route.call_count == 2


@pytest.mark.parametrize("theme_key", ["", "Bad Key!"])
async def test_shell_empty_or_unknown_theme_key_falls_back_to_default(
    bff: Bff, theme_key: str
) -> None:
    active = {"event": {**ACTIVE_EVENT["event"], "theme_key": theme_key}}
    _event_routes(bff, active=active)

    response = await bff.client.get("/v1/pages/shell")

    assert response.json()["theme"] == {"key": "default"}
    assert response.json()["event"]["key"] == "winter"  # баннер остаётся


@pytest.mark.parametrize("failure", ["503", "timeout", "contract"])
async def test_shell_progression_down_degrades_to_default_theme(bff: Bff, failure: str) -> None:
    route = bff.stubs.progression.route("GET", "/v1/events/active")
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "contract":
        route.respond(200, json={"event": {"key": 1}})
    else:
        route.respond(503)

    response = await bff.client.get("/v1/pages/shell")

    assert response.status_code == 200
    assert response.json()["theme"] == {"key": "default"}
    assert response.json()["event"] is None


async def test_shell_banner_survives_failed_event_page(bff: Bff) -> None:
    _event_routes(bff)
    bff.stubs.progression.route("GET", "/v1/events/winter").respond(503)
    _unread_route(bff)

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(bff.issuer.issue()))

    assert response.json()["event"]["found"] is None
    assert response.json()["event"]["total"] is None


async def test_shell_guest_banner_has_no_progress_and_no_page_call(bff: Bff) -> None:
    _event_routes(bff)

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(bff.issuer.issue_guest()))

    assert response.json()["event"]["found"] is None
    assert bff.stubs.progression.route("GET", "/v1/events/winter").call_count == 0


async def test_home_has_event_banner(bff: Bff) -> None:
    _event_routes(bff)
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)

    anonymous = await bff.client.get("/v1/pages/home")
    assert anonymous.json()["blocks"]["event"]["found"] is None
    assert anonymous.json()["blocks"]["event"]["key"] == "winter"


async def test_event_page_returns_page_with_theme(bff: Bff) -> None:
    _event_routes(bff)
    token = bff.issuer.issue()

    response = await bff.client.get("/v1/pages/events/winter", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    body = response.json()
    assert body["theme"] == {"key": "winter"}
    assert (body["found"], body["total"], body["map_url"]) == (1, 5, "/media/events/winter.webp")
    assert body["finds"][0]["state"] == "found"
    assert body["finds"][0]["x_pct"] == 12.5
    assert body["finds"][0]["task_i18n"] == "event.winter.snowman.task"
    route = bff.stubs.progression.route("GET", "/v1/events/winter")
    assert route.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_event_page_unknown_event_is_404(bff: Bff) -> None:
    bff.stubs.progression.route("GET", "/v1/events/ghost").respond(
        404, json={"code": "progression.event_not_found", "title": "x", "status": 404}
    )

    response = await bff.client.get(
        "/v1/pages/events/ghost", headers=bff.bearer(bff.issuer.issue())
    )

    assert_problem(response, 404, "progression.event_not_found")


async def test_event_page_anonymous_401_guest_403_without_calls(bff: Bff) -> None:
    _event_routes(bff)

    anonymous = await bff.client.get("/v1/pages/events/winter")
    guest = await bff.client.get(
        "/v1/pages/events/winter", headers=bff.bearer(bff.issuer.issue_guest())
    )

    assert_problem(anonymous, 401, "auth.unauthenticated")
    assert_problem(guest, 403, "auth.guest_forbidden")
    assert bff.stubs.progression.route("GET", "/v1/events/winter").call_count == 0


async def test_event_page_progression_down_is_503(bff: Bff) -> None:
    bff.stubs.progression.route("GET", "/v1/events/winter").respond(503)

    response = await bff.client.get(
        "/v1/pages/events/winter", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 503


async def test_profile_has_event_archive_and_survives_its_failure(bff: Bff) -> None:
    item = {
        "key": "winter",
        "title": "Зимняя охота",
        "status": "finished",
        "starts_at": "2026-12-01T00:00:00Z",
        "ends_at": "2026-12-31T00:00:00Z",
        "found": 5,
        "total": 5,
        "completed": True,
    }
    route = bff.stubs.progression.route("GET", "/v1/me/events")
    route.respond(200, json={"items": [item]})
    _progression_routes(bff)
    headers = bff.bearer(bff.issuer.issue())

    ok = await bff.client.get("/v1/pages/profile", headers=headers)
    route.respond(503)
    down = await bff.client.get("/v1/pages/profile", headers=headers)

    assert ok.json()["blocks"]["events"] == [item]
    assert down.status_code == 200
    assert down.json()["blocks"]["events"] == []


# ----------------------------------------------------------------------------- 5b: оболочка
def _unread_route(bff: Bff, count: int = 3) -> respx.Route:
    return bff.stubs.notifications.route("GET", "/v1/notifications/unread-count").respond(
        200, json={"count": count}
    )


async def test_shell_for_user_has_theme_key_and_bell(bff: Bff) -> None:
    token = bff.issuer.issue()
    unread = _unread_route(bff)

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    assert response.json() == {
        "theme": {"key": "default"},  # только ключ: значений токенов в API нет
        "bell": {"status": "ok", "data": {"unread": 3}},
        "sanctions": EMPTY_SANCTIONS,
        "event": None,
    }
    assert unread.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_shell_for_anonymous_is_public_without_bell_call(bff: Bff) -> None:
    unread = _unread_route(bff)

    response = await bff.client.get("/v1/pages/shell")

    assert response.status_code == 200
    assert response.json() == {
        "theme": {"key": "default"},
        "bell": None,
        "sanctions": None,
        "event": None,
    }
    assert response.headers["cache-control"] == PUBLIC
    assert response.headers["vary"] == "Authorization, Cookie"
    assert unread.call_count == 0


async def test_shell_for_guest_has_no_bell_and_does_not_call_notifications(bff: Bff) -> None:
    unread = _unread_route(bff)

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(bff.issuer.issue_guest()))

    assert response.status_code == 200
    assert response.json() == {
        "theme": {"key": "default"},
        "bell": None,
        "sanctions": None,
        "event": None,
    }
    assert response.headers["cache-control"] == PRIVATE  # с токеном - личный ответ
    assert unread.call_count == 0


@pytest.mark.parametrize("failure", ["503", "timeout", "401", "garbage"])
async def test_shell_notifications_failure_hides_bell_only(bff: Bff, failure: str) -> None:
    route = _unread_route(bff)
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "garbage":
        route.respond(200, json={"count": -1})
    elif failure == "401":
        route.respond(401, json={"code": "auth.unauthenticated", "title": "x", "status": 401})
    else:
        route.respond(503)

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    assert response.json() == {
        "theme": {"key": "default"},
        "bell": UNAVAILABLE,
        "sanctions": EMPTY_SANCTIONS,
        "event": None,
    }


@pytest.fixture
def theme_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ темы из окружения; фикстура идёт раньше ``bff``, чтобы Settings увидел env."""
    monkeypatch.setenv("GV_PAGES__DEFAULT_THEME_KEY", "autumn_event")


async def test_shell_theme_key_comes_from_env(theme_env: None, bff: Bff) -> None:
    response = await bff.client.get("/v1/pages/shell")

    assert response.json() == {
        "theme": {"key": "autumn_event"},
        "bell": None,
        "sanctions": None,
        "event": None,
    }


async def test_rejected_token_gets_401_on_shop_and_anonymous_shell(bff: Bff) -> None:
    """Токен не прошёл локальную проверку (подделан, истёк): источники не вызываются."""
    from datetime import timedelta

    routes = _shop_routes(bff)
    unread = _unread_route(bff)
    for token in (bff.issuer.issue(ttl=timedelta(seconds=-120)), TestTokenIssuer().issue()):
        headers = bff.bearer(token)
        assert_problem(
            await bff.client.get("/v1/pages/shop", headers=headers), 401, "auth.unauthenticated"
        )
        shell = await bff.client.get("/v1/pages/shell", headers=headers)
        assert shell.status_code == 200
        assert shell.json() == {
            "theme": {"key": "default"},
            "bell": None,
            "sanctions": None,
            "event": None,
        }
    assert all(route.call_count == 0 for route in routes.values())
    assert unread.call_count == 0


async def test_shell_theme_and_5b_schemas_in_openapi(bff: Bff) -> None:
    spec = (await bff.client.get("/openapi.json")).json()
    schemas = spec["components"]["schemas"]
    assert set(schemas["ThemeCard"]["properties"]) == {"key"}
    assert schemas["ThemeCard"]["properties"]["key"]["pattern"] == "^[a-z][a-z0-9_]*$"
    assert "501" not in spec["paths"]["/v1/pages/shop"]["get"]["responses"]
    assert "501" not in spec["paths"]["/v1/pages/shell"]["get"]["responses"]


# ----------------------------------------------------------------------------- 5c: social
FRIEND_ID = "0192a3b4-c5d6-7e8f-9a0b-000000000f01"


def _relation(bff: Bff, **fields: Any) -> respx.Route:
    return bff.stubs.social.route("GET", f"/v1/players/{PLAYER_ID}/relation").respond(
        200, json={**RELATION_OPEN, **fields}
    )


def _player_routes(bff: Bff) -> respx.Route:
    """Статистика игрока (progression) + оформление; возвращает маршрут статистики."""
    _appearance_route(bff)
    return bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress").respond(
        200, json=PLAYER
    )


async def test_player_page_social_block_and_open_profile(bff: Bff) -> None:
    token = bff.issuer.issue()
    _player_routes(bff)
    relation = _relation(bff, is_friend=True, can_send_request=False)

    response = await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(token))

    body = response.json()
    assert body["restricted"] is False
    assert body["blocks"]["social"] == {
        "status": "ok",
        "data": {"is_friend": True, "can_send_request": False, "blocked_by_me": False},
    }
    assert body["blocks"]["progress"]["status"] == "ok"
    assert relation.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_player_page_hidden_profile_is_restricted_and_stats_not_requested(bff: Bff) -> None:
    stats = _player_routes(bff)
    _relation(bff, profile_visible=False, stats_visible=False, can_send_request=False)

    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    body = response.json()
    assert body["restricted"] is True
    assert body["blocks"]["progress"] == UNAVAILABLE
    assert body["blocks"]["appearance"]["status"] == "ok"  # оформление остаётся
    assert stats.call_count == 0


async def test_player_page_hidden_stats_keeps_profile_visible(bff: Bff) -> None:
    stats = _player_routes(bff)
    _relation(bff, stats_visible=False)

    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    body = response.json()
    assert body["restricted"] is False
    assert body["blocks"]["progress"] == UNAVAILABLE
    assert body["blocks"]["social"]["status"] == "ok"
    assert stats.call_count == 0


@pytest.mark.parametrize("failure", ["503", "timeout", "401"])
async def test_player_page_social_down_hides_stats_not_page(bff: Bff, failure: str) -> None:
    """Приватность важнее доступности: без ответа social чужая статистика не отдаётся."""
    stats = _player_routes(bff)
    relation = _relation(bff)
    if failure == "timeout":
        relation.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "401":
        relation.respond(401, json={"code": "auth.unauthenticated", "title": "x", "status": 401})
    else:
        relation.respond(503)

    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    body = response.json()
    assert body["blocks"]["social"] == UNAVAILABLE
    assert body["blocks"]["progress"] == UNAVAILABLE
    assert body["restricted"] is False
    assert stats.call_count == 0


async def test_player_page_own_profile_does_not_call_social(bff: Bff) -> None:
    stats = _player_routes(bff)
    relation = _relation(bff, profile_visible=False)
    token = bff.issuer.issue(user_id=UUID(PLAYER_ID))

    response = await bff.client.get(f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(token))

    body = response.json()
    assert relation.call_count == 0
    assert body["restricted"] is False and body["blocks"]["social"] is None
    assert body["blocks"]["progress"]["status"] == "ok" and stats.call_count == 1


async def test_player_page_guest_and_anonymous_do_not_call_social(bff: Bff) -> None:
    relation = _relation(bff)
    await bff.client.get(f"/v1/pages/players/{PLAYER_ID}")
    await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue_guest())
    )
    assert relation.call_count == 0


def _friends_routes(bff: Bff) -> tuple[respx.Route, respx.Route]:
    friends = bff.stubs.social.route("GET", "/v1/me/friends").respond(
        200,
        json={
            "items": [
                {
                    "user_id": FRIEND_ID,
                    "nickname": "trinity",
                    "status": "in_match",
                    "friends_since": "2026-10-01T10:00:00Z",
                }
            ],
            "total": 1,
        },
    )
    request = {
        "id": FRIEND_ID,
        "user_id": FRIEND_ID,
        "nickname": "morpheus",
        "created_at": "2026-10-02T10:00:00Z",
    }
    requests = bff.stubs.social.route("GET", "/v1/me/friend-requests").respond(
        200, json={"incoming": [request, request], "outgoing": []}
    )
    return friends, requests


async def test_online_page_friends_block(bff: Bff) -> None:
    token = bff.issuer.issue()
    _online_routes(bff)
    friends, requests = _friends_routes(bff)

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(token))

    assert response.status_code == 200
    assert response.headers["cache-control"] == PRIVATE
    assert response.json()["blocks"]["friends"] == {
        "status": "ok",
        "data": {
            "items": [{"user_id": FRIEND_ID, "nickname": "trinity", "status": "in_match"}],
            "incoming_requests": 2,
        },
    }
    assert friends.calls.last.request.headers["authorization"] == f"Bearer {token}"
    assert requests.calls.last.request.headers["authorization"] == f"Bearer {token}"


@pytest.mark.parametrize("path", ["/v1/me/friends", "/v1/me/friend-requests"])
async def test_online_page_friends_degrade_independently(bff: Bff, path: str) -> None:
    _online_routes(bff)
    _friends_routes(bff)
    bff.stubs.social.route("GET", path).respond(503)

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["blocks"]["friends"] == UNAVAILABLE
    assert body["blocks"]["open_rooms"]["status"] == "ok"


async def test_online_page_social_401_does_not_expire_session(bff: Bff) -> None:
    _online_routes(bff)
    bff.stubs.social.route("GET", "/v1/me/friends").respond(
        401, json={"code": "auth.unauthenticated", "title": "x", "status": 401}
    )

    response = await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue()))

    assert response.json()["status"] == "ok"
    assert response.json()["blocks"]["friends"] == UNAVAILABLE


async def test_online_page_guest_and_anonymous_do_not_call_social(bff: Bff) -> None:
    friends, requests = _friends_routes(bff)
    await bff.client.get("/v1/pages/online")
    await bff.client.get("/v1/pages/online", headers=bff.bearer(bff.issuer.issue_guest()))
    assert friends.call_count == 0 and requests.call_count == 0


COMMENT = {
    "id": FRIEND_ID,
    "collection_id": str(COLLECTION_ID),
    "author_id": FRIEND_ID,
    "author_nickname": "trinity",
    "body": "Отличный сборник",
    "created_at": "2026-10-20T10:00:00Z",
    "edited_at": None,
    "can_edit": True,
    "can_delete": True,
}


def _collection_routes(bff: Bff) -> respx.Route:
    bff.stubs.collections.route("GET", f"/v1/collections/{COLLECTION_ID}").respond(
        200, json=COLLECTION_DETAIL
    )
    bff.stubs.game.route("GET", f"/v1/collections/{COLLECTION_ID}/modes").respond(
        200, json=COLLECTION_MODES
    )
    return bff.stubs.social.route("GET", f"/v1/collections/{COLLECTION_ID}/comments").respond(
        200, json={"items": [COMMENT], "total": 41, "next_cursor": "c2Vjb25k"}
    )


async def test_collection_page_comments_for_user_pass_token_and_limit(bff: Bff) -> None:
    token = bff.issuer.issue()
    comments = _collection_routes(bff)

    response = await bff.client.get(
        f"/v1/pages/collections/{COLLECTION_ID}", headers=bff.bearer(token)
    )

    assert response.status_code == 200
    block = response.json()["blocks"]["comments"]
    assert block["status"] == "ok"
    assert block["data"]["total"] == 41 and block["data"]["next_cursor"] == "c2Vjb25k"
    card = block["data"]["items"][0]
    assert (card["author_nickname"], card["can_edit"], card["can_delete"]) == (
        "trinity",
        True,
        True,
    )
    assert "collection_id" not in card
    request = comments.calls.last.request
    assert request.headers["authorization"] == f"Bearer {token}"
    assert request.url.params["limit"] == "30"


async def test_collection_page_comments_for_anonymous_and_guest_have_no_token(bff: Bff) -> None:
    comments = _collection_routes(bff)
    readonly = {**COMMENT, "can_edit": False, "can_delete": False}
    comments.respond(200, json={"items": [readonly], "total": 1, "next_cursor": None})

    anonymous = await bff.client.get(f"/v1/pages/collections/{COLLECTION_ID}")
    guest = await bff.client.get(
        f"/v1/pages/collections/{COLLECTION_ID}", headers=bff.bearer(bff.issuer.issue_guest())
    )

    assert anonymous.json()["blocks"]["comments"]["status"] == "ok"
    assert guest.json()["blocks"]["comments"]["status"] == "ok"
    assert comments.call_count == 2
    assert all("authorization" not in c.request.headers for c in comments.calls)


async def test_collection_page_comments_down_keeps_page(bff: Bff) -> None:
    comments = _collection_routes(bff)
    comments.respond(503)

    response = await bff.client.get(f"/v1/pages/collections/{COLLECTION_ID}")

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["comments"] == UNAVAILABLE
    assert blocks["collection"]["status"] == "ok" and blocks["modes"]["status"] == "ok"


async def test_shell_sanctions_for_user(bff: Bff) -> None:
    _unread_route(bff)
    token = bff.issuer.issue()
    sanctions = bff.stubs.social.route("GET", "/v1/me/sanctions").respond(
        200,
        json={
            "items": [
                {
                    "kind": "chat",
                    "started_at": "2026-10-20T10:00:00Z",
                    "expires_at": None,
                    "reason": "Спам в комментариях",
                }
            ]
        },
    )

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(token))

    assert response.headers["cache-control"] == PRIVATE
    assert response.json()["sanctions"] == {
        "status": "ok",
        "data": {"items": [{"kind": "chat", "expires_at": None, "reason": "Спам в комментариях"}]},
    }
    assert sanctions.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_shell_sanctions_failure_hides_only_that_block(bff: Bff) -> None:
    _unread_route(bff)
    bff.stubs.social.route("GET", "/v1/me/sanctions").respond(503)

    response = await bff.client.get("/v1/pages/shell", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    body = response.json()
    assert body["sanctions"] == UNAVAILABLE
    assert body["bell"]["status"] == "ok"


async def test_shell_guest_and_anonymous_do_not_call_social(bff: Bff) -> None:
    _unread_route(bff)
    sanctions = bff.stubs.social.route("GET", "/v1/me/sanctions").respond(200, json={"items": []})
    anonymous = await bff.client.get("/v1/pages/shell")
    guest = await bff.client.get("/v1/pages/shell", headers=bff.bearer(bff.issuer.issue_guest()))
    assert anonymous.json()["sanctions"] is None and guest.json()["sanctions"] is None
    assert sanctions.call_count == 0


PLAYER_1 = UUID(SAMPLES["match_result"]["players"][0]["user_id"])
PLAYER_2 = UUID(SAMPLES["match_result"]["players"][1]["user_id"])


async def _report_flags(bff: Bff, token: str) -> dict[str, bool]:
    response = await bff.client.get(f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(token))
    assert response.status_code == 200, response.text
    players = response.json()["blocks"]["match"]["data"]["players"]
    return {p["user_id"]: p["can_report"] for p in players}


async def test_match_page_can_report_for_participant_only_on_others(bff: Bff) -> None:
    _match_routes(bff)
    flags = await _report_flags(bff, bff.issuer.issue(user_id=PLAYER_1))
    assert flags == {str(PLAYER_1): False, str(PLAYER_2): True}


async def test_match_page_can_report_false_for_non_participant(bff: Bff) -> None:
    _match_routes(bff)
    flags = await _report_flags(bff, bff.issuer.issue())
    assert set(flags.values()) == {False}


async def test_match_page_can_report_false_in_solo_match(bff: Bff) -> None:
    result = SAMPLES["match_result"]
    _match_routes(bff)
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json={**result, "players": result["players"][:1]}
    )
    flags = await _report_flags(bff, bff.issuer.issue(user_id=PLAYER_1))
    assert flags == {str(PLAYER_1): False}


async def test_match_page_does_not_call_social(bff: Bff) -> None:
    _match_routes(bff)
    friends, requests = _friends_routes(bff)
    await _report_flags(bff, bff.issuer.issue(user_id=PLAYER_1))
    assert friends.call_count == 0 and requests.call_count == 0


# ----------------------------------------------------------------------------- 5c, хвосты 0069
RELATIONS_PATH = "/v1/players/relations"


def _relations_route(bff: Bff, *, can_send: set[UUID], visible: bool = True) -> respx.Route:
    """``GET /v1/players/relations``: по каждому id, звать в друзья можно ``can_send``."""

    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("user_ids")
        items = [
            {
                **RELATION_OPEN,
                "user_id": i,
                "profile_visible": visible,
                "can_send_request": UUID(i) in can_send,
                "nickname": "nick",
            }
            for i in ids
        ]
        return httpx.Response(200, json={"items": items})

    return bff.stubs.social.route("GET", RELATIONS_PATH).mock(side_effect=answer)


async def _players(bff: Bff, token: str) -> dict[str, dict[str, Any]]:
    response = await bff.client.get(f"/v1/pages/match/{MATCH_ID}", headers=bff.bearer(token))
    assert response.status_code == 200, response.text
    return {p["user_id"]: p for p in response.json()["blocks"]["match"]["data"]["players"]}


async def test_player_page_carries_nickname_from_social(bff: Bff) -> None:
    _player_routes(bff)
    _relation(bff, nickname="Trinity")
    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    assert response.json()["nickname"] == "Trinity"


async def test_player_page_nickname_is_null_when_hidden_or_social_down(bff: Bff) -> None:
    _player_routes(bff)
    _relation(bff, profile_visible=False, stats_visible=False, nickname=None)
    hidden = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    assert hidden.json()["nickname"] is None
    _relation(bff).respond(503)
    down = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    assert down.json()["nickname"] is None and down.status_code == 200


async def test_player_page_nickname_is_null_for_own_profile(bff: Bff) -> None:
    _player_routes(bff)
    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue(user_id=PLAYER_ID))
    )
    assert response.json()["nickname"] is None


async def test_appearance_passes_avatar_asset_id_when_present(bff: Bff) -> None:
    asset = "0192a3b4-c5d6-7e8f-9a0b-000000000aa1"

    def answer(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("user_ids")
        players = [
            {
                "user_id": i,
                "appearance": PROG_APPEARANCE,
                "custom_avatar_url": "/media/avatars/p.webp",
                **({"custom_avatar_asset_id": asset} if n == 0 else {}),
            }
            for n, i in enumerate(ids)
        ]
        return httpx.Response(200, json={"players": players})

    bff.stubs.progression.route("GET", "/v1/appearance").mock(side_effect=answer)
    bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress").respond(200, json=PLAYER)
    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    data = response.json()["blocks"]["appearance"]["data"]
    assert data["custom_avatar_asset_id"] == asset


async def test_appearance_without_asset_id_is_null(bff: Bff) -> None:
    _appearance_route(bff)  # старый progression: поля нет в ответе
    bff.stubs.progression.route("GET", f"/v1/users/{PLAYER_ID}/progress").respond(200, json=PLAYER)
    response = await bff.client.get(
        f"/v1/pages/players/{PLAYER_ID}", headers=bff.bearer(bff.issuer.issue())
    )
    assert response.json()["blocks"]["appearance"]["data"]["custom_avatar_asset_id"] is None


async def test_match_page_can_add_friend_comes_from_social_in_one_batch(bff: Bff) -> None:
    _match_routes(bff)
    relations = _relations_route(bff, can_send={PLAYER_2})
    players = await _players(bff, bff.issuer.issue(user_id=PLAYER_1))
    assert players[str(PLAYER_2)]["can_add_friend"] is True
    assert players[str(PLAYER_1)]["can_add_friend"] is False
    assert relations.call_count == 1
    asked = relations.calls.last.request.url.params.get_list("user_ids")
    assert asked == [str(PLAYER_2)]  # только чужие игроки, одним вызовом


async def test_match_page_can_add_friend_false_for_friend_or_closed_requests(bff: Bff) -> None:
    _match_routes(bff)
    _relations_route(bff, can_send=set())
    players = await _players(bff, bff.issuer.issue(user_id=PLAYER_1))
    assert players[str(PLAYER_2)]["can_add_friend"] is False
    assert players[str(PLAYER_2)]["can_report"] is True


@pytest.mark.parametrize("failure", ["timeout", "503", "401"])
async def test_match_page_social_failure_only_hides_add_friend(bff: Bff, failure: str) -> None:
    _match_routes(bff)
    route = _relations_route(bff, can_send={PLAYER_2})
    if failure == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "503":
        route.mock(return_value=httpx.Response(503))
    else:
        route.mock(
            return_value=httpx.Response(
                401, json={"code": "auth.unauthenticated", "title": "x", "status": 401}
            )
        )
    players = await _players(bff, bff.issuer.issue(user_id=PLAYER_1))
    assert players[str(PLAYER_2)]["can_add_friend"] is False
    assert players[str(PLAYER_2)]["can_report"] is True


async def test_match_page_non_participant_and_solo_do_not_call_social(bff: Bff) -> None:
    _match_routes(bff)
    relations = _relations_route(bff, can_send={PLAYER_1, PLAYER_2})
    await _players(bff, bff.issuer.issue())  # зритель не участник
    result = SAMPLES["match_result"]
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json={**result, "players": result["players"][:1]}
    )
    await _players(bff, bff.issuer.issue(user_id=PLAYER_1))  # соло
    assert relations.call_count == 0


async def test_match_page_relations_are_chunked_by_setting(bff: Bff) -> None:
    template = SAMPLES["match_result"]
    players = [
        {**template["players"][0], "user_id": str(UUID(int=n)), "nickname": f"p{n}", "slot": n}
        for n in range(1, 8)
    ]
    _match_routes(bff)
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json={**template, "players": players}
    )
    relations = _relations_route(bff, can_send={UUID(int=n) for n in range(2, 8)})
    flags = await _players(bff, bff.issuer.issue(user_id=UUID(int=1)))
    assert all(flags[str(UUID(int=n))]["can_add_friend"] for n in range(2, 8))
    sizes = [len(c.request.url.params.get_list("user_ids")) for c in relations.calls]
    assert sorted(sizes) == [3, 3]  # relations_batch_size = 3 в фикстуре


async def test_my_matches_card_lists_other_players_with_report_flag(bff: Bff) -> None:
    _online_routes(bff)
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(
        200, json=SAMPLES["match_result"]
    )
    response = await bff.client.get(
        "/v1/pages/online", headers=bff.bearer(bff.issuer.issue(user_id=PLAYER_1))
    )
    [card] = response.json()["blocks"]["my_matches"]["data"]["items"]
    assert card["participants"] == [
        {"user_id": str(PLAYER_2), "nickname": "trinity", "can_report": True}
    ]


async def test_my_matches_participants_degrade_to_empty(bff: Bff) -> None:
    _online_routes(bff)
    bff.stubs.match.route("GET", f"/v1/matches/{MATCH_ID}").respond(503)
    response = await bff.client.get(
        "/v1/pages/online", headers=bff.bearer(bff.issuer.issue(user_id=PLAYER_1))
    )
    body = response.json()
    assert body["status"] == "ok"
    [card] = body["blocks"]["my_matches"]["data"]["items"]
    assert card["participants"] == []


# ---------------------------------------------------------------- PR-A: друзья, награды
F1, F2, F3 = UUID(int=0xF1), UUID(int=0xF2), UUID(int=0xF3)
FILM_DAILY = "film.daily.frame"


def _feed_friends_routes(
    bff: Bff, *, results: list[dict[str, Any]], hidden: frozenset[UUID] = frozenset()
) -> dict[str, respx.Route]:
    """Три друга, результаты game по запрошенным id, видимость статистики и оформление."""
    social, game = bff.stubs.social, bff.stubs.game
    friends = [
        {"user_id": str(u), "nickname": f"friend{u.int & 0xFF:x}", "status": "offline"}
        for u in (F1, F2, F3)
    ]
    social.route("GET", "/v1/me/friends").respond(200, json={"items": friends, "total": 3})

    def relations(request: httpx.Request) -> httpx.Response:
        ids = request.url.params.get_list("user_ids")
        items = [
            {**RELATION_OPEN, "user_id": i, "stats_visible": UUID(i) not in hidden} for i in ids
        ]
        return httpx.Response(200, json={"items": items})

    def daily_results(request: httpx.Request) -> httpx.Response:
        ids = set(request.url.params.get_list("user_ids"))
        return httpx.Response(200, json=[r for r in results if r["user_id"] in ids])

    return {
        "relations": social.route("GET", RELATIONS_PATH).mock(side_effect=relations),
        "results": game.route("GET", "/internal/v1/daily/results").mock(side_effect=daily_results),
        "appearance": _appearance_route(bff),
    }


def _daily_result(user: UUID, mode_key: str, score: int) -> dict[str, Any]:
    return {
        "user_id": str(user),
        "mode_key": mode_key,
        "domain": "music" if mode_key.startswith("music") else "film",
        "day": "2026-10-10",
        "score": score,
        "max_score": 30,
    }


async def test_home_feed_shows_visible_friends_with_domain_badges_and_counter(bff: Bff) -> None:
    token = bff.issuer.issue()
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=DAILY)
    _progression_routes(bff)
    routes = _feed_friends_routes(
        bff,
        results=[
            _daily_result(F1, "music.daily.classic", 21),
            _daily_result(F1, FILM_DAILY, 12),
            _daily_result(F2, "music.daily.classic", 30),  # статистика закрыта
        ],
        hidden=frozenset({F2}),
    )

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    feed = response.json()["blocks"]["feed"]
    assert feed["status"] == "ok"
    data = feed["data"]
    assert (data["friends_total"], data["friends_played"]) == (3, 1)
    [friend] = data["items"]
    assert friend["user_id"] == str(F1) and friend["nickname"] == "friendf1"
    assert [
        (r["domain"], r["mode_key"], r["score"], r["max_score"]) for r in friend["results"]
    ] == [
        ("film", FILM_DAILY, 12, 30),
        ("music", "music.daily.classic", 21, 30),
    ]
    assert friend["appearance"]["custom_avatar_url"] == "/media/avatars/p.webp"
    # Очки закрытого друга не покидают bff; оформление - только у показанного.
    assert str(F2) not in response.text  # закрытый друг не попадает ни в items, ни в оформление
    asked = routes["results"].calls.last.request
    # Очки чужих игроков запрашиваются сервисным токеном bff_web, а не токеном зрителя.
    assert asked.headers["authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert token not in asked.headers["authorization"]
    assert sorted(asked.url.params.get_list("user_ids")) == sorted(map(str, (F1, F2, F3)))
    assert routes["appearance"].calls.last.request.url.params.get_list("user_ids") == [str(F1)]
    assert "authorization" in routes["relations"].calls.last.request.headers


async def test_home_feed_is_null_for_guest_and_anonymous_without_calls(bff: Bff) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=[])
    routes = _feed_friends_routes(bff, results=[])

    anonymous = await bff.client.get("/v1/pages/home")
    guest = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue_guest()))

    for response in (anonymous, guest):
        assert response.status_code == 200
        assert response.json()["blocks"]["feed"] is None
    assert routes["results"].call_count == 0 and routes["relations"].call_count == 0


@pytest.mark.parametrize("failure", ["503", "timeout", "garbage", "foreign-user"])
async def test_home_feed_failure_hides_only_the_feed(bff: Bff, failure: str) -> None:
    bff.stubs.game.route("GET", "/v1/modes").respond(200, json=MODES)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=DAILY)
    _progression_routes(bff)
    routes = _feed_friends_routes(bff, results=[])
    results = routes["results"]
    if failure == "timeout":
        results.mock(side_effect=httpx.ReadTimeout("slow"))
    elif failure == "garbage":
        results.mock(side_effect=None).respond(200, json={"not": "a list"})
    elif failure == "foreign-user":  # game вернул игрока, которого не спрашивали
        results.mock(side_effect=None).respond(
            200, json=[_daily_result(UUID(int=0xBAD), "music.daily.classic", 5)]
        )
    else:
        results.mock(side_effect=None).respond(503)

    response = await bff.client.get("/v1/pages/home", headers=bff.bearer(bff.issuer.issue()))

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["feed"] == UNAVAILABLE
    assert blocks["dailies"]["status"] == "ok" and blocks["progress"]["status"] == "ok"


DAILY_RESULT = {
    **RESULT,
    "mode_key": "music.daily.classic",
    "domain": "music",
    "day": "2026-10-10",
    "daily": True,
    "score": 20,
}
REWARDS_DONE = {
    "session_id": str(SESSION),
    "settled": True,
    "xp": 60,
    "verses": 5,
    "level_before": 6,
    "level_after": 7,
    "streak": {"domain": "music", "current": 3},
}


async def test_results_page_daily_has_rewards_and_friends_today(bff: Bff) -> None:
    token = bff.issuer.issue()
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=DAILY_RESULT)
    bff.stubs.game.route("GET", "/v1/daily").respond(200, json=DAILY)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)
    rewards = bff.stubs.progression.route("GET", "/v1/me/rewards").respond(200, json=REWARDS_DONE)
    _feed_friends_routes(
        bff,
        results=[
            _daily_result(F1, "music.daily.classic", 27),
            _daily_result(F2, "music.daily.classic", 10),
            _daily_result(F3, FILM_DAILY, 29),
        ],
    )

    response = await bff.client.get(f"/v1/pages/results/{SESSION}", headers=bff.bearer(token))

    assert response.status_code == 200, response.text
    blocks = response.json()["blocks"]
    assert blocks["rewards"] == {
        "status": "ok",
        "data": {
            "settled": True,
            "xp": 60,
            "verses": 5,
            "level_before": 6,
            "level_after": 7,
            "leveled_up": True,
            "streak": {"domain": "music", "current": 3},
        },
    }
    today = blocks["friends_today"]["data"]
    assert (today["mode_key"], today["domain"], today["day"]) == (
        "music.daily.classic",
        "music",
        "2026-10-10",
    )
    assert (today["my_score"], today["my_place"]) == (20, 2)
    assert (today["friends_total"], today["friends_played"]) == (3, 2)
    assert today["average_score"] == 18.5
    assert [(r["user_id"], r["score"]) for r in today["items"]] == [(str(F1), 27), (str(F2), 10)]
    assert blocks["result"]["data"]["mode_key"] == "music.daily.classic"
    assert dict(rewards.calls.last.request.url.params) == {"session_id": str(SESSION)}
    assert rewards.calls.last.request.headers["authorization"] == f"Bearer {token}"


async def test_results_page_not_daily_has_rewards_but_no_friends_today(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)
    bff.stubs.progression.route("GET", "/v1/me/rewards").respond(200, json=REWARDS_DONE)
    routes = _feed_friends_routes(bff, results=[])

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    blocks = response.json()["blocks"]
    assert blocks["rewards"]["status"] == "ok"
    assert blocks["friends_today"] is None
    assert routes["results"].call_count == 0


async def test_results_page_rewards_pending_is_ok_not_an_error(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=RESULT)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)
    rewards = bff.stubs.progression.route("GET", "/v1/me/rewards")  # по умолчанию: pending
    rewards.respond(200, json=REWARDS_PENDING)

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    assert response.json()["blocks"]["rewards"]["data"]["settled"] is False
    assert rewards.call_count == 3  # первая попытка и два повтора из настроек


async def test_results_page_extra_blocks_fail_independently(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=DAILY_RESULT)
    bff.stubs.game.route("GET", "/v1/daily").respond(503)  # нужен для периода «среди друзей»
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)
    bff.stubs.progression.route("GET", "/v1/me/rewards").respond(503)
    _feed_friends_routes(bff, results=[])

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue())
    )

    assert response.status_code == 200
    blocks = response.json()["blocks"]
    assert blocks["rewards"] == UNAVAILABLE
    assert blocks["friends_today"] == UNAVAILABLE
    assert blocks["result"]["status"] == "ok" and blocks["items"]["status"] == "ok"


async def test_results_page_for_guest_skips_rewards_and_friends(bff: Bff) -> None:
    bff.stubs.game.route("GET", f"/v1/sessions/{SESSION}/result").respond(200, json=DAILY_RESULT)
    bff.stubs.catalog.route("GET", f"/v1/items/{ITEM}").respond(200, json=CARD)
    rewards = bff.stubs.progression.route("GET", "/v1/me/rewards")
    routes = _feed_friends_routes(bff, results=[])

    response = await bff.client.get(
        f"/v1/pages/results/{SESSION}", headers=bff.bearer(bff.issuer.issue_guest())
    )

    blocks = response.json()["blocks"]
    assert blocks["rewards"] is None and blocks["friends_today"] is None
    assert rewards.call_count == 0 and routes["results"].call_count == 0


def test_new_blocks_are_optional_in_the_contract() -> None:
    spec = json.loads(
        (Path(__file__).resolve().parents[4] / "contracts" / "openapi" / "bff_web.json").read_text(
            "utf-8"
        )
    )
    schemas = spec["components"]["schemas"]
    for model, fields in {
        "HomeBlocks": {"feed"},
        "ResultsBlocks": {"rewards", "friends_today"},
        "FriendDailyCard": {"appearance"},
    }.items():
        assert fields <= set(schemas[model]["properties"])
        assert not fields & set(schemas[model].get("required", []))
