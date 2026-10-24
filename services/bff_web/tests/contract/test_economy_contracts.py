"""Контракты экранов 5b (магазин, оболочка, профиль, игрок): источники и снимок bff.

Примеры ответов progression и notifications валидны по их снимкам OpenAPI, а ответы экранов,
собранные из них настоящими клиентами и use-case, - по снимку ``contracts/openapi/bff_web.json``.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any
from uuid import UUID

import httpx
import pytest
import respx
from bff_web_service.api.public.schemas import PlayerPage, ProfilePage, ShellPage, ShopPage
from bff_web_service.application.dto import SubjectKind, Viewer
from bff_web_service.application.queries import (
    GetPlayerPage,
    GetProfilePage,
    GetShellPage,
    GetShopPage,
)
from bff_web_service.infrastructure.clients import HttpNotificationsClient, HttpProgressionClient
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp
from jsonschema import Draft202012Validator, FormatChecker

from gv_testkit.contracts import contracts_dir

pytestmark = pytest.mark.contract

USER = Viewer(token="user-token", kind=SubjectKind.USER, verified=True)
PLAYER = UUID(int=900)


def _item(n: int, slot: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": str(UUID(int=n)),
        "key": f"item_{n}",
        "slot": slot,
        "rarity": "common",
        "title": f"Предмет {n}",
        "media_kind": "animated",
        "asset_url": f"/media/{n}.webp",
        "poster_url": f"/media/{n}.png",
        "level_required": 3,
        "price": 100,
        "event_key": None,
        **extra,
    }


APPEARANCE = {
    "slots": [{"slot": "frame", "item": _item(1, "frame")}, {"slot": "title", "item": None}]
}
SAMPLES: dict[tuple[str, str], Any] = {
    ("progression", "/v1/shop/items"): {
        "items": [{"item": _item(1, "frame"), "state": "available"}],
        "balance": 10,
    },
    ("progression", "/v1/shop/packs"): {
        "packs": [
            {
                "id": str(UUID(int=5)),
                "key": "starter",
                "title": "Стартовый",
                "style": "classic",
                "items": [_item(1, "frame")],
                "verses_amount": 0,
                "price_kopecks": 100,
            }
        ]
    },
    ("progression", "/v1/me/inventory"): {
        "items": [
            {
                "item": _item(1, "frame"),
                "source": "level",
                "acquired_at": "2026-10-20T10:00:00Z",
                "expires_at": None,
                "equipped": False,
            }
        ]
    },
    ("progression", "/v1/me/appearance"): APPEARANCE,
    ("progression", "/v1/me/avatar"): {
        "entitled": False,
        "current_url": None,
        "request": {
            "asset_id": str(UUID(int=8)),
            "status": "rejected",
            "submitted_at": "2026-10-21T10:00:00Z",
            "reason_code": "bad_format",
        },
        "limits": {"max_bytes": 1000, "formats": ["webp"], "min_side_px": 64, "max_side_px": 512},
    },
    ("progression", "/v1/appearance"): {
        "players": [
            {"user_id": str(PLAYER), "appearance": APPEARANCE, "custom_avatar_url": "/a/p.webp"}
        ]
    },
    ("notifications", "/v1/notifications/unread-count"): {"count": 4},
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


@pytest.mark.parametrize(("service", "path"), list(SAMPLES))
def test_upstream_samples_match_producer_snapshots(service: str, path: str) -> None:
    assert _errors(service, _response_schema(service, path), SAMPLES[(service, path)]) == []


def _http(service: str, base: str) -> UpstreamHttp:
    return UpstreamHttp(
        service,
        httpx.AsyncClient(base_url=base),
        CircuitBreaker(failures=3, reset_s=60),
        timeout_s=0.5,
        cache=None,
    )


@pytest.fixture
def upstreams() -> Any:
    with respx.mock(assert_all_called=False) as router:
        bases = {
            "progression": "http://progression.test",
            "notifications": "http://notifications.test",
        }
        for (service, path), body in SAMPLES.items():
            router.get(f"{bases[service]}{path}").respond(200, json=body)
        router.get(f"http://progression.test/v1/users/{PLAYER}/progress").respond(
            200, json={"user_id": str(PLAYER), "level": 1, "total_xp": 0, "badges": []}
        )
        for path, body in {
            "/v1/me/progress": _PROGRESS,
            "/v1/me/wallet": {"earned": 0, "paid": 0, "total": 0},
            "/v1/me/quests": {"daily": {"items": []}, "weekly": {"items": []}},
            "/v1/me/badges": {"items": []},
            "/v1/wheel": {"enabled": False, "sectors": [], "today": None, "next_spin_at": None},
        }.items():
            router.get(f"http://progression.test{path}").respond(200, json=body)
        yield router


_PROGRESS = {
    "level": 1,
    "max_level": 100,
    "total_xp": 0,
    "xp_in_level": 0,
    "xp_to_next": 100,
    "solo_xp_today": 0,
    "solo_xp_cap": 100,
    "streaks": [],
    "boost": None,
}


def _bff_errors(model: Any, page: Any, path: str) -> list[str]:
    body = model.model_validate(page, from_attributes=True).model_dump(mode="json")
    return _errors("bff_web", _response_schema("bff_web", path), body)


async def test_shop_page_matches_bff_snapshot(upstreams: respx.MockRouter) -> None:
    progression = HttpProgressionClient(_http("progression", "http://progression.test"))
    page = await GetShopPage(progression, concurrency=5)(USER)

    assert _bff_errors(ShopPage, page, "/v1/pages/shop") == []
    assert page.blocks.catalog.is_ok and page.blocks.packs is not None and page.blocks.packs.is_ok


async def test_shell_page_matches_bff_snapshot(upstreams: respx.MockRouter) -> None:
    notifications = HttpNotificationsClient(_http("notifications", "http://notifications.test"))
    shell = GetShellPage(notifications, theme_key="default", concurrency=5)

    for viewer in (USER, Viewer()):
        page = await shell(viewer)
        assert _bff_errors(ShellPage, page, "/v1/pages/shell") == []
    assert page.bell is None
    assert (await shell(USER)).bell is not None


async def test_profile_and_player_pages_match_bff_snapshot(upstreams: respx.MockRouter) -> None:
    progression = HttpProgressionClient(_http("progression", "http://progression.test"))

    profile = await GetProfilePage(progression, concurrency=5)(USER)
    player = await GetPlayerPage(progression, concurrency=5)(USER, PLAYER)

    assert _bff_errors(ProfilePage, profile, "/v1/pages/profile") == []
    assert _bff_errors(PlayerPage, player, "/v1/pages/players/{user_id}") == []
    assert all(b is not None and b.is_ok for b in vars_of(profile.blocks))
    assert player.blocks.appearance is not None and player.blocks.appearance.is_ok


def vars_of(blocks: Any) -> list[Any]:
    return [getattr(blocks, name) for name in blocks.__slots__]
