"""Клиенты онлайна: счётчик social (общий кэш), комната и название в match."""

from __future__ import annotations

import httpx
import pytest
import respx
from bff_web_service.application.ports import UpstreamUnavailableError
from bff_web_service.infrastructure.clients import HttpMatchClient, HttpSocialClient
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp

pytestmark = pytest.mark.unit

SOCIAL = "http://social.test"
MATCH = "http://match.test"
CODE = "K7M2QX9P"


class MemoryCache:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self.data[key], self.ttl[key] = value, ttl_s


def http(service: str, base: str, cache: MemoryCache | None = None) -> UpstreamHttp:
    return UpstreamHttp(
        service,
        httpx.AsyncClient(base_url=base),
        CircuitBreaker(failures=5, reset_s=10),
        timeout_s=1.0,
        cache=cache,
    )


@respx.mock
async def test_online_count_is_public_cached_and_sent_without_token() -> None:
    route = respx.get(f"{SOCIAL}/v1/presence/online-count").respond(200, json={"count": 128})
    cache = MemoryCache()
    client = HttpSocialClient(
        http("social", SOCIAL, cache), online_count_ttl_s=7, cache_prefix="t:"
    )

    assert await client.online_count() == 128
    assert await client.online_count() == 128  # второй раз - из кэша

    assert route.call_count == 1
    assert "authorization" not in route.calls.last.request.headers
    assert cache.ttl == {"t:social:online_count:v1": 7}


@respx.mock
async def test_online_count_out_of_contract_is_unavailable_and_not_cached() -> None:
    respx.get(f"{SOCIAL}/v1/presence/online-count").respond(200, json={"count": -1})
    cache = MemoryCache()
    client = HttpSocialClient(http("social", SOCIAL, cache))

    with pytest.raises(UpstreamUnavailableError):
        await client.online_count()

    assert cache.data == {}


ROOM = {
    "code": CODE,
    "mode": {
        "key": "film.lobby.collection",
        "version": 1,
        "domain": "film",
        "kind": "lobby",
        "title_key": "modes.film.lobby.collection.title",
        "clue_type": "frame_progressive",
        "answer_format": "classic",
    },
    "host_id": "0192a3b4-c5d6-7e8f-9a0b-000000000001",
    "players": [
        {
            "slot": 0,
            "user_id": "0192a3b4-c5d6-7e8f-9a0b-000000000001",
            "nickname": "Host",
            "is_host": True,
            "presence": "online",
            "ready": True,
            "score": 0,
        }
    ],
    "players_min": 2,
    "settings": {
        "rounds": 7,
        "level_window_s": 15,
        "answer_format": "classic",
        "clue_type": "frame_progressive",
        "players_max": 6,
        "visibility": "public",
        "collection_ids": [],
        "name": "Вечер кино",
    },
    "phase": "lobby",
    "version": 3,
    "server_now": "2026-10-30T12:00:00Z",
}


@respx.mock
async def test_room_keeps_host_given_name_and_players() -> None:
    respx.get(f"{MATCH}/v1/rooms/{CODE}").respond(200, json=ROOM)

    room = await HttpMatchClient(http("match", MATCH)).room("user-token", CODE)

    assert room.settings.name == "Вечер кино"
    assert [(p.nickname, p.is_host, p.level) for p in room.players] == [("Host", True, None)]
    assert room.phase == "lobby"


@respx.mock
async def test_room_name_is_optional_and_other_code_is_a_violation() -> None:
    old = {**ROOM, "settings": {k: v for k, v in ROOM["settings"].items() if k != "name"}}
    respx.get(f"{MATCH}/v1/rooms/{CODE}").respond(200, json=old)
    client = HttpMatchClient(http("match", MATCH))

    assert (await client.room("t", CODE)).settings.name is None

    respx.get(f"{MATCH}/v1/rooms/AAAAAAA2").respond(200, json=old)  # ответ про другую комнату
    with pytest.raises(UpstreamUnavailableError):
        await client.room("t", "AAAAAAA2")
