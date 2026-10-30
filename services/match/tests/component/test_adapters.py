"""Адаптеры внешних сервисов на заглушках (R5, R6): game, media-pipeline, Centrifugo.

Заглушки ``gv_testkit.stub_service`` проверяют, что вызываемая ручка есть в контракте
вызываемого сервиса (у Centrifugo контракта нет — путь проверяется тестом).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import jwt
import pytest
from match_service.application.owner import plan_error
from match_service.application.ports import RoundPlanRequest
from match_service.domain.errors import MatchUnavailableError
from match_service.infrastructure.breaker import CircuitBreaker
from match_service.infrastructure.clients import GameClient, MediaLinks
from match_service.infrastructure.realtime import (
    REALTIME_PUBLISH,
    CentrifugoPublisher,
    SubscriptionTokenIssuer,
)
from pydantic import SecretStr

from gv_core.errors import DependencyUnavailableError, GoneError
from gv_core.ids import uuid7
from gv_game_kernel import AssetRef
from gv_service import RealtimeSettings
from gv_testkit import stub_service

pytestmark = pytest.mark.component

GAME = "http://game.test"
MEDIA = "http://media.test"
CENTRIFUGO = "http://centrifugo.test/api"
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


class Tokens:
    def __init__(self) -> None:
        self.invalidated = 0

    async def token(self) -> str:
        return "service-token"

    def invalidate(self) -> None:
        self.invalidated += 1


def _plan_body(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": {"key": request["mode_key"], "version": 3, "domain": "film", "body": {"k": 1}},
        "seed": request["seed"],
        "rounds": [
            {
                "index": 0,
                "answer": {
                    "media_item_id": str(uuid7()),
                    "domain": "film",
                    "kind": "film",
                    "title": "Брат",
                    "year": 1997,
                    "aliases": ["брат"],
                    "title_aliases": ["брат"],
                    "persons": [
                        {
                            "person_id": str(uuid7()),
                            "name": "Алексей Балабанов",
                            "normalized": "алексей балабанов",
                            "role": "director",
                        }
                    ],
                    "partial": [],
                },
                "levels": [{"level": 1, "asset_set_id": str(uuid7())}],
            }
        ],
        "collections": [],
    }


REQUEST = RoundPlanRequest(mode_key="film.party.frame", version=3, seed="a" * 32, rounds=1)


async def test_plan_artwork_set_is_optional_for_old_and_new_game() -> None:
    """Порядок выкладки (ADR 0073): старый game без поля -> `None`, новый -> набор."""
    client = GameClient(GAME, Tokens(), timeout_s=1, breaker=CircuitBreaker(2, 60))
    artwork_set = uuid7()
    try:
        with stub_service(GAME, service="game") as game:
            route = game.route("POST", "/internal/v1/round-plans")
            route.mock(side_effect=lambda r: httpx.Response(200, json=_plan_body(_json(r))))
            old = await client.create_round_plan(REQUEST)
            assert old.rounds[0].artwork_asset_set_id is None

            def with_artwork(request: httpx.Request) -> httpx.Response:
                body = _plan_body(_json(request))
                body["rounds"][0]["artwork_asset_set_id"] = str(artwork_set)
                return httpx.Response(200, json=body)

            route.mock(side_effect=with_artwork)
            new = await client.create_round_plan(REQUEST)
            assert new.rounds[0].artwork_asset_set_id == artwork_set

            def null_artwork(request: httpx.Request) -> httpx.Response:
                body = _plan_body(_json(request))
                body["rounds"][0]["artwork_asset_set_id"] = None
                return httpx.Response(200, json=body)

            route.mock(side_effect=null_artwork)
            assert (await client.create_round_plan(REQUEST)).rounds[0].artwork_asset_set_id is None
    finally:
        await client.aclose()


async def test_game_plan_parsed_and_errors_mapped() -> None:
    tokens = Tokens()
    client = GameClient(GAME, tokens, timeout_s=1, breaker=CircuitBreaker(2, 60))
    try:
        with stub_service(GAME, service="game") as game:
            route = game.route("POST", "/internal/v1/round-plans")
            route.mock(side_effect=lambda r: httpx.Response(200, json=_plan_body(_json(r))))
            plan = await client.create_round_plan(REQUEST)
            assert plan.mode_version == 3 and plan.rounds[0].answer.title == "Брат"
            sent = _json(route.calls.last.request)
            assert sent["exclude_ids"] == [] and sent["rounds"] == 1
            assert route.calls.last.request.headers["authorization"] == "Bearer service-token"

            route.mock(
                return_value=httpx.Response(
                    422, json={"code": "game.pool_too_small", "details": {"available": 2}}
                )
            )
            with pytest.raises(Exception) as small:
                await client.create_round_plan(REQUEST)
            assert small.value.code == "match.pool_too_small"  # type: ignore[attr-defined]

            route.mock(return_value=httpx.Response(401, json={"code": "auth.unauthenticated"}))
            with pytest.raises(MatchUnavailableError):
                await client.create_round_plan(REQUEST)
            assert tokens.invalidated == 1

            route.mock(return_value=httpx.Response(503))
            for _ in range(2):
                with pytest.raises(MatchUnavailableError):
                    await client.create_round_plan(REQUEST)
            calls = route.call_count
            with pytest.raises(MatchUnavailableError):  # breaker разомкнут: без вызова
                await client.create_round_plan(REQUEST)
            assert route.call_count == calls
    finally:
        await client.aclose()


def test_plan_error_translates_fake_and_game_codes() -> None:
    assert plan_error(DependencyUnavailableError("down")).code == "match.game_unavailable"
    gone = plan_error(DependencyUnavailableError("assets", code="game.assets_unavailable"))
    assert gone.code == "match.content_unavailable"


async def test_game_mode_body_is_cached_per_version() -> None:
    client = GameClient(GAME, Tokens(), timeout_s=1, breaker=CircuitBreaker(5, 60))
    try:
        with stub_service(GAME, service="game") as game:
            route = game.route("GET", "/internal/v1/modes/film.party.frame/versions/3")
            route.respond(
                200,
                json={"key": "film.party.frame", "version": 3, "domain": "film", "body": {"x": 1}},
            )
            assert await client.body("film.party.frame", 3) == {"x": 1}
            assert await client.body("film.party.frame", 3) == {"x": 1}
            assert route.call_count == 1
            game.route("GET", "/internal/v1/modes/film.party.frame/versions/9").respond(404)
            assert await client.body("film.party.frame", 9) is None
    finally:
        await client.aclose()


async def test_media_link_and_gone() -> None:
    links = MediaLinks(MEDIA, Tokens(), timeout_s=1, breaker=CircuitBreaker(5, 60))
    asset = AssetRef(asset_set_id=uuid7(), levels_count=2)
    try:
        with stub_service(MEDIA, service="media_pipeline") as media:
            route = media.route("POST", "/internal/v1/asset-links")
            route.respond(
                200,
                json={
                    "url": "/media/a/x.jpg",
                    "expires_at": "2026-10-22T12:10:00+00:00",
                    "mime": "image/jpeg",
                    "level": 1,
                },
            )
            link = await links.link(asset, 1, "room:K7MQ2ZXA:0")
            assert link.url == "/media/a/x.jpg"
            assert _json(route.calls.last.request)["subject"] == "room:K7MQ2ZXA:0"
            route.respond(410)
            with pytest.raises(GoneError):
                await links.link(asset, 2, "room:K7MQ2ZXA:0")
            route.respond(503)
            with pytest.raises(DependencyUnavailableError):
                await links.link(asset, 2, "room:K7MQ2ZXA:0")
    finally:
        await links.aclose()


async def test_centrifugo_publish_and_failures_are_swallowed() -> None:
    settings = RealtimeSettings(api_url=CENTRIFUGO, api_key=SecretStr("k" * 40))
    publisher = CentrifugoPublisher(settings)
    message = {"version": 1, "kind": "closed", "server_now": NOW.isoformat(), "delta": {}}
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/publish")
            route.respond(200, json={"result": {}})
            await publisher.publish("room:K7MQ2ZXA", message)
            request = route.calls.last.request
            assert request.headers["x-api-key"] == "k" * 40
            assert _json(request) == {"channel": "room:K7MQ2ZXA", "data": message}
            route.respond(200, json={"error": {"code": 102, "message": "unknown channel"}})
            await publisher.publish("room:K7MQ2ZXA", message)
            route.mock(side_effect=httpx.ConnectError("down"))
            await publisher.publish("room:K7MQ2ZXA", message)  # без исключения
    finally:
        await publisher.aclose()


def _count(label: str) -> float:
    return REALTIME_PUBLISH.labels(label)._value.get()


async def test_centrifugo_unsubscribe_reads_api_error_from_body() -> None:
    settings = RealtimeSettings(api_url=CENTRIFUGO, api_key=SecretStr("k" * 40))
    publisher = CentrifugoPublisher(settings)
    user = uuid7()
    try:
        with stub_service(CENTRIFUGO) as centrifugo:
            route = centrifugo.route("POST", "/unsubscribe")
            route.respond(200, json={"result": {}})
            ok_before = _count("unsubscribe_ok")
            await publisher.unsubscribe(user, "room:K7MQ2ZXA")
            assert _json(route.calls.last.request) == {
                "user": str(user),
                "channel": "room:K7MQ2ZXA",
            }
            assert _count("unsubscribe_ok") == ok_before + 1
            # 200 с ошибкой API в теле — не успех (отписка не произошла).
            route.respond(200, json={"error": {"code": 102, "message": "unknown channel"}})
            errors_before = _count("unsubscribe_api_error")
            await publisher.unsubscribe(user, "room:K7MQ2ZXA")
            assert _count("unsubscribe_api_error") == errors_before + 1
            assert _count("unsubscribe_ok") == ok_before + 1
    finally:
        await publisher.aclose()


def test_subscription_token_claims() -> None:
    secret = "s" * 40
    issuer = SubscriptionTokenIssuer(
        RealtimeSettings(subscription_token_secret=SecretStr(secret), token_ttl_s=300)
    )
    user = uuid7()
    token, expires = issuer.issue(user, "room:K7MQ2ZXA", NOW)
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience="centrifugo",
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["sub"] == str(user) and claims["channel"] == "room:K7MQ2ZXA"
    assert claims["exp"] - claims["iat"] == 300 and expires.timestamp() == claims["exp"]
    with pytest.raises(MatchUnavailableError):
        SubscriptionTokenIssuer(None).issue(user, "room:K7MQ2ZXA", NOW)


def _json(request: httpx.Request) -> Any:
    import json

    return json.loads(request.content)


async def test_game_plan_sends_overrides_only_when_chosen_and_maps_refusals() -> None:
    client = GameClient(GAME, Tokens(), timeout_s=1, breaker=CircuitBreaker(5, 60))
    chosen = RoundPlanRequest(
        mode_key="film.lobby.collection",
        version=1,
        seed="a" * 32,
        rounds=1,
        clue_type="poster_masked",
        answer_format="pick",
    )
    try:
        with stub_service(GAME, service="game") as game:
            route = game.route("POST", "/internal/v1/round-plans")
            route.mock(side_effect=lambda r: httpx.Response(200, json=_plan_body(_json(r))))
            await client.create_round_plan(REQUEST)
            assert "overrides" not in _json(route.calls.last.request)
            await client.create_round_plan(chosen)
            assert _json(route.calls.last.request)["overrides"] == {
                "clue_type": "poster_masked",
                "answer_format": "pick",
            }

            route.mock(
                return_value=httpx.Response(
                    422, json={"code": "game.override_not_allowed", "details": {"field": "clue"}}
                )
            )
            with pytest.raises(Exception) as denied:
                await client.create_round_plan(chosen)
            assert denied.value.code == "match.setting_not_allowed"  # type: ignore[attr-defined]
            assert denied.value.details == {  # type: ignore[attr-defined]
                "field": "clue_type",
                "reason": "not_allowed",
            }

            errors = [{"path": "session.contest.type", "code": "config.answer_format_unsupported"}]
            route.mock(
                return_value=httpx.Response(
                    422, json={"code": "game.override_invalid", "details": {"errors": errors}}
                )
            )
            with pytest.raises(Exception) as invalid:
                await client.create_round_plan(chosen)
            assert invalid.value.details == {  # type: ignore[attr-defined]
                "field": "overrides",
                "reason": "incompatible",
                "errors": errors,
            }
    finally:
        await client.aclose()
