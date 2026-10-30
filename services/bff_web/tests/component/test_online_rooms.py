"""Экран комнаты и уровни хостов лобби: match + progression под сервисным токеном bff_web."""

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

from gv_core.ids import uuid7
from gv_service import Infrastructure, create_app
from gv_testkit import InfraProvider, TestTokenIssuer, assert_problem, service_client, stub_service
from gv_testkit.stubs import ServiceStub

pytestmark = pytest.mark.component

MATCH, PROGRESSION, IDENTITY = (
    "http://match.test",
    "http://progression.test",
    "http://identity.test",
)
CODE = "K7M2QX9P"
HOST = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000e01")
GUEST_PLAYER = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000e02")
SERVICE_TOKEN = TestTokenIssuer().issue_service("bff_web", "progression", ["levels.read"])
PRIVATE = "private, no-store"

MODE = {
    "key": "film.lobby.frame",
    "version": 2,
    "domain": "film",
    "kind": "lobby",
    "title_key": "modes.film.lobby.frame.title",
    "clue_type": "frame_progressive",
    "answer_format": "classic",
}
SETTINGS = {
    "rounds": 10,
    "level_window_s": 20,
    "answer_format": "classic",
    "clue_type": "frame_progressive",
    "players_max": 6,
    "visibility": "public",
    "collection_ids": [],
}


def slot(n: int, user_id: UUID, nickname: str, *, host: bool) -> dict[str, Any]:
    return {
        "slot": n,
        "user_id": str(user_id),
        "nickname": nickname,
        "is_host": host,
        "presence": "online",
        "ready": host,
        "score": 0,
    }


def room_view(name: str | None) -> dict[str, Any]:
    return {
        "code": CODE,
        "mode": MODE,
        "host_id": str(HOST),
        "players": [
            slot(0, HOST, "neo_2026", host=True),
            slot(1, GUEST_PLAYER, "anna", host=False),
        ],
        "players_min": 2,
        "settings": {**SETTINGS, "name": name},
        "chat": {"emotes": [], "phrases": [], "free_text": False},
        "phase": "lobby",
        "version": 4,
        "server_now": "2026-10-30T12:00:00Z",
    }


@dataclass
class Rooms:
    client: httpx.AsyncClient
    match: ServiceStub
    progression: ServiceStub
    issuer: TestTokenIssuer

    def bearer(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def stubs() -> Iterator[tuple[ServiceStub, ServiceStub]]:
    with ExitStack() as stack:
        identity = stack.enter_context(stub_service(IDENTITY, service="identity"))
        identity.route("POST", "/internal/v1/service-tokens").respond(
            200, json={"access_token": SERVICE_TOKEN, "token_type": "Bearer", "expires_in": 600}
        )
        yield (
            stack.enter_context(stub_service(MATCH, service="match")),
            stack.enter_context(stub_service(PROGRESSION, service="progression")),
        )


@pytest.fixture
async def rooms(
    infra: InfraProvider, stubs: tuple[ServiceStub, ServiceStub], token_issuer: TestTokenIssuer
) -> AsyncIterator[Rooms]:
    settings = Settings(
        environment="ci",
        redis_url=SecretStr(infra.redis_url()),
        upstreams={
            "match_url": MATCH,
            "progression_url": PROGRESSION,
            "identity_url": IDENTITY,
            "timeout_s": 0.5,
        },
        page_cache={"key_prefix": f"test:bff:{uuid7().hex[-12:]}:"},
        service_auth={
            "token_url": f"{IDENTITY}/internal/v1/service-tokens",
            "client_secret": "test-service-secret",
        },
    )
    runtime = await Infrastructure.open(settings, DEFINITION.tables)
    try:
        app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
        async with service_client(app) as client:
            yield Rooms(client, stubs[0], stubs[1], token_issuer)
    finally:
        await runtime.close()


def levels_route(rooms: Rooms) -> Any:
    return rooms.progression.route("GET", "/internal/v1/levels").respond(
        200, json={"items": [{"user_id": str(HOST), "level": 12, "xp_in_level": 40}]}
    )


async def test_room_page_adds_levels_under_service_token_and_default_title(rooms: Rooms) -> None:
    rooms.match.route("GET", f"/v1/rooms/{CODE}").respond(200, json=room_view(None))
    levels = levels_route(rooms)
    token = rooms.issuer.issue()

    response = await rooms.client.get(f"/v1/pages/rooms/{CODE}", headers=rooms.bearer(token))

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == PRIVATE
    room = response.json()["blocks"]["room"]["data"]
    assert room["title"] == "Комната neo_2026"
    assert room["settings"]["name"] is None
    assert [(p["nickname"], p["level"]) for p in room["players"]] == [
        ("neo_2026", 12),
        ("anna", None),  # progression не вернул игрока
    ]
    asked = levels.calls.last.request
    assert asked.headers["authorization"] == f"Bearer {SERVICE_TOKEN}"  # не токен зрителя
    assert token not in asked.headers["authorization"]
    assert sorted(asked.url.params.get_list("user_ids")) == sorted(map(str, (HOST, GUEST_PLAYER)))


async def test_room_page_keeps_name_entered_by_host(rooms: Rooms) -> None:
    rooms.match.route("GET", f"/v1/rooms/{CODE}").respond(200, json=room_view("Вечер кино"))
    levels_route(rooms)

    response = await rooms.client.get(
        f"/v1/pages/rooms/{CODE}", headers=rooms.bearer(rooms.issuer.issue())
    )

    room = response.json()["blocks"]["room"]["data"]
    assert (room["title"], room["settings"]["name"]) == ("Вечер кино", "Вечер кино")


async def test_room_page_progression_down_leaves_levels_empty_page_whole(rooms: Rooms) -> None:
    rooms.match.route("GET", f"/v1/rooms/{CODE}").respond(200, json=room_view(None))
    rooms.progression.route("GET", "/internal/v1/levels").respond(
        503, json={"code": "progression.config_unavailable", "status": 503}
    )

    response = await rooms.client.get(
        f"/v1/pages/rooms/{CODE}", headers=rooms.bearer(rooms.issuer.issue())
    )

    assert response.status_code == 200
    room = response.json()["blocks"]["room"]
    assert room["status"] == "ok"
    assert [p["level"] for p in room["data"]["players"]] == [None, None]


async def test_room_page_access_rules_and_missing_room(rooms: Rooms) -> None:
    route = rooms.match.route("GET", f"/v1/rooms/{CODE}").respond(200, json=room_view(None))
    path = f"/v1/pages/rooms/{CODE}"

    assert_problem(await rooms.client.get(path), 401, "auth.unauthenticated")
    guest = await rooms.client.get(path, headers=rooms.bearer(rooms.issuer.issue_guest()))
    assert_problem(guest, 403, "auth.guest_forbidden")
    assert route.call_count == 0
    bad_code = await rooms.client.get(
        "/v1/pages/rooms/abc", headers=rooms.bearer(rooms.issuer.issue())
    )
    assert bad_code.status_code == 422

    route.respond(404, json={"code": "match.room_not_found", "title": "Not found", "status": 404})
    missing = await rooms.client.get(path, headers=rooms.bearer(rooms.issuer.issue()))
    assert_problem(missing, 404, "match.room_not_found")


async def test_room_page_match_down_is_unavailable_block(rooms: Rooms) -> None:
    rooms.match.route("GET", f"/v1/rooms/{CODE}").mock(side_effect=httpx.ConnectTimeout("slow"))

    response = await rooms.client.get(
        f"/v1/pages/rooms/{CODE}", headers=rooms.bearer(rooms.issuer.issue())
    )

    assert response.status_code == 200
    assert response.json() == {"blocks": {"room": {"status": "unavailable", "data": None}}}
