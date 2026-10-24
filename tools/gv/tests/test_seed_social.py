"""Шаг ``gv dev seed`` для social: ожидание опубликованного конфига (bootstrap, ADR 0053).

Шаг ничего не создаёт: проверяем, что он ждёт публикацию опросом, не мутирует social и
падает по таймауту, если конфиг не опубликован.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from gv_cli.seed import SeedConfig, Seeder, SeedError

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]


@dataclass
class FakeSocial:
    """Admin-API social: список версий конфига; публикация появляется после N опросов."""

    publish_after: int = 0  # сколько опросов отвечать «только черновик»
    statuses: list[str] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)
    polls: int = 0

    def client_factory(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/internal/v1/service-tokens":
            return httpx.Response(200, json={"access_token": "svc-tok"})
        if path == "/internal/v1/admin-sessions":
            return httpx.Response(200, json={"access_token": "admin-tok"})
        assert request.headers["Authorization"] == "Bearer admin-tok", path
        if request.method == "GET" and path == "/admin/v1/config/versions":
            self.polls += 1
            if self.polls <= self.publish_after:
                return httpx.Response(200, json={"items": []})
            return httpx.Response(
                200, json={"items": [{"version": 1, "status": s} for s in self.statuses]}
            )
        return httpx.Response(404, json={"code": "not_found", "status": 404})


def _run(stack: FakeSocial) -> None:
    config = SeedConfig(
        identity_url="http://identity",
        catalog_url="http://catalog",
        media_url="http://media",
        game_url="http://game",
        collections_url="http://collections",
        social_url="http://social",
        admin_login="a@example.com",
        admin_password="x",
        service_secret="s",
        repo_root=REPO,
        poll_interval_s=0.0,
        assets_timeout_s=0.05,
    )
    with Seeder(config, client_factory=stack.client_factory, echo=lambda _m: None) as seeder:
        seeder.authenticate()
        seeder.seed_social()


def test_returns_when_config_published_and_does_not_mutate() -> None:
    stack = FakeSocial(statuses=["published"])

    _run(stack)

    assert [r.method for r in stack.requests if r.url.path.startswith("/admin/")] == ["GET"]


def test_waits_for_bootstrap_publication() -> None:
    stack = FakeSocial(publish_after=3, statuses=["published"])

    _run(stack)

    assert stack.polls == 4


def test_times_out_when_only_draft_exists() -> None:
    stack = FakeSocial(statuses=["draft", "archived"])

    with pytest.raises(SeedError, match=r"social.config.bootstrap"):
        _run(stack)
