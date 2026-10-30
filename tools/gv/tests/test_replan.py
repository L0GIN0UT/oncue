"""``gv dev replan-assets``: порции пересоздания, ожидание наборов и новых версий режимов.

Фейк стека отвечает через ``httpx.MockTransport`` и записывает запросы.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from gv_cli.replan import Replanner
from gv_cli.seed import SeedConfig, SeedError

pytestmark = pytest.mark.unit

OLD = {"levels": [{"blur_px": 48, "crop": 0.35, "max_width": 960}]}
NEW = {"levels": [{"blur_px": 25, "crop": 0.35, "max_width": 960}]}


class FakeStack:
    """identity, media и game в одном обработчике; режим обновится после ``flip_after`` опросов."""

    def __init__(self, *, flip_after: int | None = 2) -> None:
        self.flip_after = flip_after
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.polls = 0
        self.pending_polls = 1

    def client_factory(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body))
        if path == "/internal/v1/service-tokens":
            return httpx.Response(200, json={"access_token": "svc"})
        if path == "/internal/v1/admin-sessions":
            return httpx.Response(200, json={"access_token": "adm"})
        if path == "/admin/v1/profiles":
            self.pending_polls -= 1
            pending = max(self.pending_polls, 0)
            if (
                "clue_type" in dict(request.url.params)
                and request.url.params["clue_type"] == "none"
            ):
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                json=[
                    {
                        "clue_type": "frame_progressive",
                        "profile_key": "default",
                        "origin": "static",
                        "domains": ["film"],
                        "sets": {"ready": 1, "pending": pending, "failed": 0},
                    }
                ],
            )
        if path == "/admin/v1/asset-sets/replan":
            after = body["after"]
            if after is None:
                nxt, scheduled = "0192a3b4-c5d6-7e8f-9a0b-000000000001", 3
            else:
                nxt, scheduled = None, 2
            return httpx.Response(
                200, json={"processed": scheduled, "scheduled": scheduled, "next_after": nxt}
            )
        if path == "/admin/v1/mode-configs/film.daily.frame":
            return httpx.Response(200, json={"published_version": self._version()})
        if path.startswith("/admin/v1/mode-configs/film.daily.frame/versions/"):
            version = int(path.rsplit("/", 1)[1])
            params = NEW if version == 2 else OLD
            return httpx.Response(200, json={"body": {"clue": {"params": params}}})
        return httpx.Response(404, json={"code": "not_found"})

    def _version(self) -> int:
        self.polls += 1
        if self.flip_after is not None and self.polls > self.flip_after:
            return 2
        return 1


def _config(tmp_path: Path) -> SeedConfig:
    modes = tmp_path / "modes"
    modes.mkdir()
    (modes / "film.daily.frame.yaml").write_text(
        "key: film.daily.frame\nclue:\n  type: frame_progressive\n  params:\n    levels:\n"
        "      - { blur_px: 25, crop: 0.35, max_width: 960 }\n",
        encoding="utf-8",
    )
    (modes / "music.daily.classic.yaml").write_text(
        "key: music.daily.classic\nclue:\n  type: audio_intro\n  params: {start_s: 0}\n",
        encoding="utf-8",
    )
    return SeedConfig(
        identity_url="http://identity",
        catalog_url="http://catalog",
        media_url="http://media",
        game_url="http://game",
        collections_url="http://collections",
        admin_login="a@b.c",
        admin_password="p",
        service_secret="s",
        repo_root=tmp_path,
        modes_dir=modes,
        poll_interval_s=0.0,
        assets_timeout_s=5.0,
    )


def _replanner(stack: FakeStack, tmp_path: Path) -> Replanner:
    return Replanner(_config(tmp_path), client_factory=stack.client_factory, echo=lambda _m: None)


def test_replan_walks_cursor_pages_per_domain_with_unique_keys(tmp_path: Path) -> None:
    stack = FakeStack()
    with _replanner(stack, tmp_path) as seeder:
        seeder.authenticate()
        assert seeder.replan("frame_progressive", batch=3) == {"film": 5}
    posts = [c for c in stack.calls if c[1] == "/admin/v1/asset-sets/replan"]
    assert [p[2]["after"] for p in posts] == [None, "0192a3b4-c5d6-7e8f-9a0b-000000000001"]
    assert all(p[2]["domain"] == "film" and p[2]["limit"] == 3 for p in posts)


def test_wait_modes_polls_until_game_publishes_new_version(tmp_path: Path) -> None:
    stack = FakeStack(flip_after=2)
    with _replanner(stack, tmp_path) as seeder:
        seeder.authenticate()
        seeder.wait_modes("frame_progressive")
    assert stack.polls == 3
    asked = {c[1] for c in stack.calls if c[1].startswith("/admin/v1/mode-configs/")}
    assert not any("music" in p for p in asked)  # режимы других загадок не ждём


def test_wait_modes_times_out_with_names_of_waiting_modes(tmp_path: Path) -> None:
    stack = FakeStack(flip_after=None)
    config = _config(tmp_path)
    config = dataclasses.replace(config, assets_timeout_s=0.05)
    with Replanner(config, client_factory=stack.client_factory, echo=lambda _m: None) as seeder:
        seeder.authenticate()
        with pytest.raises(SeedError, match=r"film\.daily\.frame"):
            seeder.wait_modes("frame_progressive")


def test_wait_profiles_after_replan_waits_for_pending_sets(tmp_path: Path) -> None:
    stack = FakeStack()
    stack.pending_polls = 3
    with _replanner(stack, tmp_path) as seeder:
        seeder.authenticate()
        seeder.wait_profiles()
    assert sum(1 for c in stack.calls if c[1] == "/admin/v1/profiles") == 3
