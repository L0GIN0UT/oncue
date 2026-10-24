"""Шаг ``gv dev seed`` для событий progression (5d, ADR 0054): определения из
``seeds/progression/events`` через ``/admin/v1/events``.

Фейковый progression на ``httpx.MockTransport`` по контракту OpenAPI: создание (201), чтение
с запусками, замена тела (409 при запуске ``published/active``), загрузка карты.
Отдельно проверяется, что все сиды событий проходят JSON Schema ``event_definition.v1.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

from gv_cli.seed import SeedConfig, Seeder, SeedError

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
EVENTS = REPO / "seeds" / "progression" / "events"
SCHEMA = REPO / "contracts" / "progression" / "event_definition.v1.json"
SEED_FILES = sorted(EVENTS.glob("*.yaml"))
EVENT_KEYS = {"new_year_2026", "halloween"}
_EVENT = r"/admin/v1/events/([a-z][a-z0-9_]*)"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@dataclass
class FakeEvents:
    """Admin-API событий progression."""

    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    status: dict[tuple[str, str], int] = field(default_factory=dict)

    def client_factory(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self.handle))

    def mutations(self) -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if r.method in {"POST", "PUT"} and r.url.path.startswith("/admin/v1/")
        ]

    def sent(self, method: str, pattern: str) -> list[httpx.Request]:
        return [
            r for r in self.requests if r.method == method and re.fullmatch(pattern, r.url.path)
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - маршрутизатор фейка
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/internal/v1/service-tokens":
            return httpx.Response(200, json={"access_token": "svc"})
        if path == "/internal/v1/admin-sessions":
            return httpx.Response(200, json={"access_token": "adm"})
        assert request.headers["Authorization"] == "Bearer adm"
        if method in {"POST", "PUT"}:
            assert len(request.headers["Idempotency-Key"]) >= 8
        for (m, pattern), code in self.status.items():
            if m == method and re.fullmatch(pattern, path):
                return httpx.Response(code, json={"code": f"progression.test_{code}"})
        if method == "POST" and path == "/admin/v1/events":
            body = json.loads(request.content)
            assert len(body["reason"]) >= 3
            if body["key"] in self.events:
                return httpx.Response(409, json={"code": "progression.event_key_taken"})
            self.events[body["key"]] = {
                "key": body["key"],
                "body": body["body"],
                "map_url": None,
                "runs": [],
            }
            return httpx.Response(201, json=self.events[body["key"]])
        found = re.fullmatch(_EVENT, path)
        if found and found[1] not in self.events:
            return httpx.Response(404, json={"code": "progression.event_not_found"})
        if found and method == "GET":
            return httpx.Response(200, json=self.events[found[1]])
        if found and method == "PUT":
            event = self.events[found[1]]
            if any(r["status"] in {"published", "active"} for r in event["runs"]):
                return httpx.Response(409, json={"code": "progression.event_definition_locked"})
            event["body"] = json.loads(request.content)["body"]
            return httpx.Response(200, json=event)
        found = re.fullmatch(_EVENT + "/map", path)
        if found and method == "PUT":
            assert b'name="file"' in request.content and b'name="reason"' in request.content
            event = self.events[found[1]]
            event["map_url"] = f"https://cdn.test/events/{found[1]}.png"
            return httpx.Response(200, json=event)
        return httpx.Response(404, json={"code": "not_found"})


def _seeder(stack: FakeEvents, progression_dir: Path) -> Seeder:
    config = SeedConfig(
        identity_url="http://identity",
        catalog_url="http://catalog",
        media_url="http://media",
        game_url="http://game",
        collections_url="http://collections",
        progression_url="http://progression",
        progression_dir=progression_dir,
        admin_login="a@example.com",
        admin_password="x",
        service_secret="s",
        repo_root=REPO,
        poll_interval_s=0.0,
        assets_timeout_s=0.05,
    )
    return Seeder(config, client_factory=stack.client_factory, echo=lambda _m: None)


def _run(stack: FakeEvents, progression_dir: Path) -> None:
    with _seeder(stack, progression_dir) as seeder:
        seeder.authenticate()
        seeder.seed_events()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Копия сидов событий во временном каталоге (карт-картинок нет)."""
    events = tmp_path / "events"
    events.mkdir()
    for path in SEED_FILES:
        (events / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------ валидация сидов
def test_seed_files_are_present() -> None:
    assert {p.stem for p in SEED_FILES} >= EVENT_KEYS


@pytest.mark.parametrize("path", SEED_FILES, ids=lambda p: p.name)
def test_seed_body_matches_event_definition_schema(path: Path) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    seed = _load(path)

    errors = [e.message for e in Draft202012Validator(schema).iter_errors(seed["body"])]

    assert errors == []
    assert seed["key"] == path.stem


def test_new_year_has_expected_shape() -> None:
    body = _load(EVENTS / "new_year_2026.yaml")["body"]
    finds = body["finds"]

    assert len(finds) == 7
    assert body["theme_key"] == "winter"
    assert body["reward"]["item_key"] == "frame_winter_2026"
    assert [f["number"] for f in finds] == list(range(1, 8))
    assert sum(f["trigger"]["type"] == "server" for f in finds) == 5
    assert [f["key"] for f in finds if f["final"]] == ["star_on_top"]
    # Точки разнесены по карте.
    assert len({(f["x_pct"], f["y_pct"]) for f in finds}) == 7


def test_reward_item_exists_in_items_seed() -> None:
    items = _load(REPO / "seeds" / "progression" / "items" / "items.yaml")["items"]
    frame = next(i for i in items if i["key"] == "frame_winter_2026")

    assert frame["slot"] == "frame"
    assert (
        REPO / "seeds" / "progression" / "items" / "images" / "frame_winter_2026.webp"
    ).is_file()


# ------------------------------------------------------------------ шаг seed_events
def test_creates_events_with_body_and_reason(root: Path) -> None:
    stack = FakeEvents()

    _run(stack, root)

    assert set(stack.events) == EVENT_KEYS
    assert stack.events["new_year_2026"]["body"] == _load(EVENTS / "new_year_2026.yaml")["body"]
    assert not stack.sent("PUT", r".*/map")  # файла карты нет
    assert not stack.sent("POST", r".*/runs")  # запуски создаёт владелец, не сид


def test_rerun_makes_no_mutations(root: Path) -> None:
    stack = FakeEvents()
    _run(stack, root)
    done = len(stack.mutations())

    _run(stack, root)

    assert len(stack.mutations()) == done


def test_changed_seed_updates_event_without_active_runs(root: Path) -> None:
    stack = FakeEvents()
    _run(stack, root)
    stack.events["halloween"]["runs"] = [{"status": "draft"}, {"status": "finished"}]
    path = root / "events" / "halloween.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Хеллоуин\n", "Хеллоуин 2\n", 1),
        encoding="utf-8",
    )

    _run(stack, root)

    assert stack.events["halloween"]["body"]["title"] == "Хеллоуин 2"
    assert len(stack.sent("PUT", r"/admin/v1/events/halloween")) == 1


@pytest.mark.parametrize("run_status", ["published", "active"])
def test_locked_event_is_not_updated(root: Path, run_status: str) -> None:
    stack = FakeEvents()
    _run(stack, root)
    stack.events["halloween"]["runs"] = [{"status": run_status}]
    path = root / "events" / "halloween.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("Хеллоуин\n", "Хеллоуин 2\n", 1),
        encoding="utf-8",
    )

    _run(stack, root)

    assert stack.events["halloween"]["body"]["title"] == "Хеллоуин"
    assert not stack.sent("PUT", r"/admin/v1/events/halloween")


def test_uploads_map_when_file_exists_and_only_once(root: Path) -> None:
    (root / "events" / "new_year_2026.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBPmap")
    stack = FakeEvents()

    _run(stack, root)
    _run(stack, root)

    assert len(stack.sent("PUT", r"/admin/v1/events/new_year_2026/map")) == 1
    assert stack.events["new_year_2026"]["map_url"]


def test_unexpected_error_is_reported_with_code(root: Path) -> None:
    stack = FakeEvents(status={("POST", r"/admin/v1/events"): 422})

    with pytest.raises(SeedError, match=r"создание события halloween.*422.*progression.test_422"):
        _run(stack, root)


def test_no_events_dir_skips_step(tmp_path: Path) -> None:
    stack = FakeEvents()

    _run(stack, tmp_path)

    assert not [r for r in stack.requests if "/events" in r.url.path]
