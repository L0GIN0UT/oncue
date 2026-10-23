"""Контракты экранов онлайна (этап 4a/4b, §8).

Цепочка проверяется с двух концов:

* примеры ответов источников (``tests/data/match.json``), на которых держатся component-тесты,
  валидны по OpenAPI-снимкам производителей (match, game, library): тест не «договорится»
  с телом, которого match не отдаст;
* те же примеры, пройдя HTTP-клиенты и use-case bff, дают ответ экрана, валидный по снимку
  ``contracts/openapi/bff_web.json`` — его читает фронтенд.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx
from bff_web_service.api.public.schemas import LeaderboardsPage, MatchPage, OnlinePage
from bff_web_service.application.dto import SubjectKind, Viewer
from bff_web_service.application.queries import GetLeaderboardsPage, GetMatchPage, GetOnlinePage
from bff_web_service.infrastructure.clients import (
    HttpCatalogClient,
    HttpGameClient,
    HttpLibraryClient,
    HttpMatchClient,
    HttpRatingClient,
)
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp
from jsonschema import Draft202012Validator, FormatChecker

from gv_testkit.contracts import contracts_dir

pytestmark = pytest.mark.contract

SAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "data" / "match.json").read_text(encoding="utf-8")
)
RATING_SAMPLES: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "data" / "rating.json").read_text(encoding="utf-8")
)
MATCH_ID = UUID(SAMPLES["match_result"]["match_id"])
USER = Viewer(token="user-token", kind=SubjectKind.USER, verified=True)
CARD: dict[str, Any] = {
    "id": "",
    "domain": "film",
    "kind": "movie",
    "title": "Брат",
    "original_title": None,
    "year": 1997,
    "genres": [],
    "persons": [],
}


@cache
def _spec(service: str) -> dict[str, Any]:
    path = contracts_dir() / "openapi" / f"{service}.json"
    spec: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return spec


def _errors(service: str, schema: dict[str, Any], value: Any) -> list[str]:
    root = {**schema, "components": _spec(service)["components"]}
    validator = Draft202012Validator(root, format_checker=FormatChecker())
    return [f"{list(e.absolute_path)}: {e.message}" for e in validator.iter_errors(value)]


def _response_schema(service: str, path: str) -> dict[str, Any]:
    op = _spec(service)["paths"][path]["get"]
    schema: dict[str, Any] = op["responses"]["200"]["content"]["application/json"]["schema"]
    return schema


@pytest.mark.parametrize(
    ("service", "path", "sample"),
    [
        ("match", "/v1/rooms/open", "open_rooms"),
        ("match", "/v1/me/matches", "my_matches"),
        ("match", "/v1/matches/{match_id}", "match_result"),
        ("game", "/v1/modes", "online_modes"),
        ("library", "/v1/items/{media_item_id}/shelves", "item_shelves"),
    ],
)
def test_upstream_samples_match_producer_contracts(service: str, path: str, sample: str) -> None:
    assert _errors(service, _response_schema(service, path), SAMPLES[sample]) == []


@pytest.mark.parametrize(
    ("service", "path", "sample"),
    [
        ("match", "/v1/queue/pool", "queue_pool"),
        ("rating", "/v1/ladders", "ladders"),
        ("rating", "/v1/me/ratings", "my_ratings"),
        ("rating", "/v1/ladders/{ladder}/weekly", "weekly"),
        ("rating", "/v1/ladders/{ladder}/leaderboard", "leaderboard"),
    ],
)
def test_rating_samples_match_producer_contracts(service: str, path: str, sample: str) -> None:
    assert _errors(service, _response_schema(service, path), RATING_SAMPLES[sample]) == []


def test_online_modes_sample_is_session_online_answer() -> None:
    """Ответ ``session=online``: у каждого режима блок ``online`` и ``allow_guest = false``."""
    schema = _response_schema("game", "/v1/modes")
    assert _errors("game", schema, SAMPLES["online_modes"]) == []
    online = _spec("game")["components"]["schemas"]["OnlineModeOut"]
    for mode in SAMPLES["online_modes"]:
        assert mode["online"] is not None
        assert mode["allow_guest"] is False and mode["presets"] is None
        assert _errors("game", online, mode["online"]) == []


def _params_errors(service: str, path: str, params: dict[str, str]) -> list[str]:
    """Query-параметры вызова описаны в снимке ручки и проходят их схемы."""
    described = {
        p["name"]: p["schema"]
        for p in _spec(service)["paths"][path]["get"].get("parameters", [])
        if p["in"] == "query"
    }
    errors: list[str] = []
    for name, raw in params.items():
        schema = described.get(name)
        if schema is None:
            errors.append(f"{name}: нет в contracts/openapi/{service}.json {path}")
            continue
        value: Any = int(raw) if raw.isdigit() else raw
        errors += [f"{name}={raw}: {e}" for e in _errors(service, schema, value)]
    return errors


def _http(service: str, base: str) -> UpstreamHttp:
    client = httpx.AsyncClient(base_url=base)
    return UpstreamHttp(
        service, client, CircuitBreaker(failures=3, reset_s=60), timeout_s=0.5, cache=None
    )


@pytest.fixture
def upstreams() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://match.test/v1/rooms/open", name="rooms").respond(
            200, json=SAMPLES["open_rooms"]
        )
        router.get("http://match.test/v1/me/matches", name="matches").respond(
            200, json=SAMPLES["my_matches"]
        )
        router.get(f"http://match.test/v1/matches/{MATCH_ID}").respond(
            200, json=SAMPLES["match_result"]
        )
        router.get("http://game.test/v1/modes", name="modes").respond(
            200, json=SAMPLES["online_modes"]
        )
        router.get("http://match.test/v1/queue/pool", name="pool").respond(
            200, json=RATING_SAMPLES["queue_pool"]
        )
        router.get("http://rating.test/v1/ladders", name="ladders").respond(
            200, json=RATING_SAMPLES["ladders"]
        )
        router.get("http://rating.test/v1/me/ratings", name="mine").respond(
            200, json=RATING_SAMPLES["my_ratings"]
        )
        for ladder in ("film", "music"):
            router.get(
                f"http://rating.test/v1/ladders/{ladder}/weekly", name=f"weekly_{ladder}"
            ).respond(200, json={**RATING_SAMPLES["weekly"], "ladder": ladder})
            router.get(
                f"http://rating.test/v1/ladders/{ladder}/leaderboard", name=f"board_{ladder}"
            ).respond(200, json={**RATING_SAMPLES["leaderboard"], "ladder": ladder})
        for rnd in SAMPLES["match_result"]["rounds"]:
            item_id = rnd["media_item_id"]
            router.get(f"http://catalog.test/v1/items/{item_id}").respond(
                200, json={**CARD, "id": item_id}
            )
            router.get(f"http://library.test/v1/items/{item_id}/shelves").respond(
                200, json={"media_item_id": item_id, "shelves": ["system:watched"]}
            )
        yield router


def _bff_schema(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


async def test_online_page_matches_bff_snapshot(upstreams: respx.MockRouter) -> None:
    query = GetOnlinePage(
        HttpMatchClient(_http("match", "http://match.test")),
        HttpGameClient(_http("game", "http://game.test"), modes_ttl_s=30, cache_prefix="t:"),
        HttpRatingClient(_http("rating", "http://rating.test"), ttl_s=10, cache_prefix="t:"),
        concurrency=10,
        rooms_limit=20,
        matches_limit=10,
        weekly_limit=5,
    )

    for viewer in (USER, Viewer(token_rejected=True), Viewer()):
        page = OnlinePage.model_validate(await query(viewer), from_attributes=True)
        body = page.model_dump(mode="json")
        assert _errors("bff_web", _response_schema("bff_web", "/v1/pages/online"), body) == []
        if viewer is USER:
            assert all(b["status"] == "ok" for b in body["blocks"].values())
            assert all(m["online"] for m in body["blocks"]["modes"]["data"])
    assert body == {"status": "auth_required", "blocks": None}

    # Запросы bff к источникам — с параметрами, которые источники описали.
    calls = {
        ("game", "/v1/modes"): upstreams["modes"],
        ("match", "/v1/rooms/open"): upstreams["rooms"],
        ("match", "/v1/me/matches"): upstreams["matches"],
        ("match", "/v1/queue/pool"): upstreams["pool"],
        ("rating", "/v1/ladders"): upstreams["ladders"],
        ("rating", "/v1/me/ratings"): upstreams["mine"],
    }
    for (service, path), route in calls.items():
        params = dict(route.calls.last.request.url.params)
        assert _params_errors(service, path, params) == []
    assert dict(upstreams["modes"].calls.last.request.url.params) == {"session": "online"}


async def test_match_page_matches_bff_snapshot(upstreams: respx.MockRouter) -> None:
    query = GetMatchPage(
        HttpMatchClient(_http("match", "http://match.test")),
        HttpCatalogClient(_http("catalog", "http://catalog.test"), item_ttl_s=1, cache_prefix="t:"),
        HttpLibraryClient(_http("library", "http://library.test")),
        concurrency=10,
        items_deadline_s=5,
    )

    page = MatchPage.model_validate(await query(USER, MATCH_ID), from_attributes=True)
    body = page.model_dump(mode="json")

    assert _errors("bff_web", _bff_schema("MatchPage"), body) == []
    assert _errors("bff_web", _response_schema("bff_web", "/v1/pages/match/{match_id}"), body) == []
    assert all(b["status"] == "ok" for b in body["blocks"].values())


def _leaderboards_query() -> GetLeaderboardsPage:
    rating = HttpRatingClient(_http("rating", "http://rating.test"), ttl_s=10, cache_prefix="t:")
    return GetLeaderboardsPage(rating, concurrency=10)


async def test_leaderboards_page_matches_bff_snapshot(upstreams: respx.MockRouter) -> None:
    page = await _leaderboards_query()(Viewer(), "film", "c1", around_me=False)
    body = LeaderboardsPage.model_validate(page, from_attributes=True).model_dump(mode="json")

    assert _errors("bff_web", _bff_schema("LeaderboardsPage"), body) == []
    assert _errors("bff_web", _response_schema("bff_web", "/v1/pages/leaderboards"), body) == []
    assert all(b["status"] == "ok" for b in body["blocks"].values())
    # Параметры вызовов описаны в снимках rating.
    board = upstreams["board_film"].calls.last.request
    path = "/v1/ladders/{ladder}/leaderboard"
    assert _params_errors("rating", path, dict(board.url.params)) == []
    assert dict(board.url.params) == {"cursor": "c1"}


async def test_leaderboards_around_me_params_match_rating_snapshot(
    upstreams: respx.MockRouter,
) -> None:
    await _leaderboards_query()(USER, "film", None, around_me=True)

    params = dict(upstreams["board_film"].calls.last.request.url.params)
    assert params == {"around": "me"}
    assert _params_errors("rating", "/v1/ladders/{ladder}/leaderboard", params) == []
