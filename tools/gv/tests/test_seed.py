"""Тесты оркестратора ``gv dev seed`` на фейковом HTTP-стеке (без реального окружения).

Фейк отвечает через ``httpx.MockTransport`` и записывает запросы: проверяем цепочку
аутентификации, использование admin-токена, импорт обоих файлов, загрузку исходника на
единицу, перегенерацию дейликов на нужный день, идемпотентность и опрос готовности.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from gv_cli.seed import SeedConfig, Seeder, SeedError

pytestmark = pytest.mark.unit

# (ключ сида, домен, media_item_id) — согласованы между каталогом, фейком и генератором.
ITEMS = [
    ("film:interstellar", "film", "11111111-1111-1111-1111-111111111111"),
    ("track:bohemian-rhapsody", "music", "22222222-2222-2222-2222-222222222222"),
]
DAY = "2026-10-13"


@dataclass
class FakeStack:
    """Фейковый стек Guessverse: маршрутизация по пути, запись запросов и настройки готовности."""

    assets_ready_after: int = 0  # сколько опросов asset-sets вернут pending до ready
    import_status: str = "done"  # статус задания импорта в ответе GET
    item_detail_status: int = 200  # для проверки неожиданного ответа каталога
    daily_status: int = 200  # 409 — набор уже есть
    playable_count: int = 10  # сколько играбельных единиц отдаёт проекция game
    requests: list[httpx.Request] = field(default_factory=list)
    _asset_polls: dict[str, int] = field(default_factory=dict)

    def client_factory(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self.handle))

    def _recorded(self, method: str, pattern: str) -> list[httpx.Request]:
        return [
            r for r in self.requests if r.method == method and re.fullmatch(pattern, r.url.path)
        ]

    def _routes(self) -> list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]]:
        return [
            ("POST", r"/internal/v1/service-tokens", self._service_token),
            ("POST", r"/internal/v1/admin-sessions", self._admin_session),
            ("POST", r"/admin/v1/imports", self._imports_post),
            ("GET", r"/admin/v1/imports/[0-9a-f-]+", self._import_state),
            ("GET", r"/admin/v1/items", self._items_list),
            ("GET", r"/admin/v1/items/[0-9a-f-]+", self._item_detail),
            ("POST", r"/admin/v1/sources", self._sources_post),
            ("GET", r"/admin/v1/asset-sets", self._asset_sets),
            ("GET", r"/admin/v1/projection/items", self._projection),
            ("POST", r"/admin/v1/daily-sets/.+/regenerate", self._daily_post),
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        # Все admin-ручки обязаны нести admin-токен.
        if path.startswith("/admin/v1/"):
            assert request.headers["Authorization"] == "Bearer admin-tok", path
        for route_method, pattern, responder in self._routes():
            if method == route_method and re.fullmatch(pattern, path):
                return responder(request)
        return httpx.Response(404, json={"code": "not_found"})

    def _service_token(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "Authorization" not in request.headers
        assert body["audience"] == "identity"
        assert body["client_id"] and body["client_secret"]
        return httpx.Response(
            200, json={"access_token": "svc-tok", "token_type": "Bearer", "expires_in": 600}
        )

    def _admin_session(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer svc-tok"
        body = json.loads(request.content)
        assert body["login"] and body["password"]
        return httpx.Response(
            200,
            json={
                "access_token": "admin-tok",
                "refresh_token": "r",
                "expires_in": 900,
                "user": {"id": "u"},
                "identity_version": 1,
            },
        )

    def _import_state(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "import_id": request.url.path.rsplit("/", 1)[-1],
                "source": "file",
                "ref": None,
                "file_id": "x.jsonl",
                "status": self.import_status,
                "counters": {"created": 2, "updated": 0, "unchanged": 0, "failed": 0},
                "error": "boom" if self.import_status == "failed" else None,
            },
        )

    def _imports_post(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-import-")
        body = json.loads(request.content)
        assert body["source"] == "file"
        assert len(body["reason"]) >= 3
        seq = len(self._recorded("POST", "/admin/v1/imports"))
        return httpx.Response(
            202,
            json={
                "import_id": f"00000000-0000-0000-0000-00000000000{seq}",
                "source": "file",
                "ref": None,
                "file_id": body["file_id"],
                "status": "pending",
                "counters": {"created": 0, "updated": 0, "unchanged": 0, "failed": 0},
                "error": None,
            },
        )

    def _items_list(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": item_id,
                        "domain": domain,
                        "kind": domain,
                        "title": seed,
                        "year": None,
                        "status": "active",
                        "version": 1,
                    }
                    for seed, domain, item_id in ITEMS
                ],
                "next_cursor": None,
            },
        )

    def _item_detail(self, request: httpx.Request) -> httpx.Response:
        if self.item_detail_status != 200:
            return httpx.Response(self.item_detail_status, json={"code": "catalog.unavailable"})
        item_id = request.url.path.rsplit("/", 1)[-1]
        seed, domain, _ = next(i for i in ITEMS if i[2] == item_id)
        return httpx.Response(
            200,
            json={
                "id": item_id,
                "domain": domain,
                "kind": domain,
                "title": seed,
                "original_title": None,
                "year": None,
                "status": "active",
                "blocked_reason_code": None,
                "version": 1,
                "subtitle": None,
                "aliases": [],
                "persons": [],
                "genres": [],
                "external_refs": {"seed": seed},
                "metrics": {},
                "source_media": [],
            },
        )

    def _sources_post(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-source-")
        assert request.headers["content-type"].startswith("multipart/form-data")
        content = request.content
        assert b'name="media_item_id"' in content
        assert b'name="kind"' in content
        assert b'name="reason"' in content
        assert b'name="file"' in content
        media_item_id = next(i[2] for i in ITEMS if i[2].encode() in content)
        return httpx.Response(
            201,
            json={
                "source_media_id": "33333333-3333-3333-3333-333333333333",
                "media_item_id": media_item_id,
                "kind": "audio" if b"\r\n\r\naudio" in content else "frame",
                "sha256": "x",
                "bytes": 10,
                "mime": "audio/mp4",
                "scheduled_asset_set_ids": [],
            },
        )

    def _asset_sets(self, request: httpx.Request) -> httpx.Response:
        media_item_id = request.url.params["media_item_id"]
        polls = self._asset_polls.get(media_item_id, 0)
        self._asset_polls[media_item_id] = polls + 1
        status = "ready" if polls >= self.assets_ready_after else "pending"
        return httpx.Response(
            200,
            json=[
                {
                    "asset_set_id": "44444444-4444-4444-4444-444444444444",
                    "media_item_id": media_item_id,
                    "clue_type": "audio_intro",
                    "profile_key": "default",
                    "params_hash": "h",
                    "status": status,
                    "levels_count": 6,
                    "generation": 1,
                    "error_code": None,
                }
            ],
        )

    def _projection(self, request: httpx.Request) -> httpx.Response:
        # Проекция game с играбельными единицами под режим (сидер ждёт пул перед набором дня).
        assert request.url.params.get("playable_for_mode")
        return httpx.Response(
            200,
            json=[
                {
                    "media_item_id": f"01a0000000007000800000000000000{i:1x}",
                    "domain": "film",
                    "kind": "film",
                    "title": f"item {i}",
                    "year": None,
                    "status": "active",
                    "playable": True,
                }
                for i in range(self.playable_count)
            ],
        )

    def _daily_post(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-daily-")
        body = json.loads(request.content)
        assert len(body["reason"]) >= 3
        mode_key = request.url.path.split("/")[4]
        if self.daily_status != 200:
            return httpx.Response(self.daily_status, json={"code": "game.daily_locked"})
        return httpx.Response(
            200,
            json={
                "mode_key": mode_key,
                "day": DAY,
                "mode_version": 1,
                "status": "scheduled",
                "items": [],
            },
        )


_DAILY_YAML = """\
key: {key}
domain: {domain}
source:
  type: daily
  params:
    timezone: Europe/Moscow
"""

_RANDOM_YAML = """\
key: film.random.frame
domain: film
source:
  type: random
  params: {}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Временный репозиторий: файлы импорта, сиды режимов и каталог исходников."""
    catalog = tmp_path / "seeds" / "catalog"
    catalog.mkdir(parents=True)
    films = [i for i in ITEMS if i[1] == "film"]
    tracks = [i for i in ITEMS if i[1] == "music"]
    (catalog / "films.jsonl").write_text(
        "\n".join(json.dumps({"key": k, "kind": "film", "title": k}) for k, _, _ in films),
        encoding="utf-8",
    )
    (catalog / "tracks.jsonl").write_text(
        "# комментарий\n"
        + "\n".join(json.dumps({"key": k, "kind": "track", "title": k}) for k, _, _ in tracks),
        encoding="utf-8",
    )
    modes = tmp_path / "seeds" / "game" / "modes"
    modes.mkdir(parents=True)
    (modes / "film.daily.frame.yaml").write_text(
        _DAILY_YAML.format(key="film.daily.frame", domain="film"), encoding="utf-8"
    )
    (modes / "music.daily.classic.yaml").write_text(
        _DAILY_YAML.format(key="music.daily.classic", domain="music"), encoding="utf-8"
    )
    (modes / "film.random.frame.yaml").write_text(_RANDOM_YAML, encoding="utf-8")
    (tmp_path / "seeds" / "media" / "out").mkdir(parents=True)
    return tmp_path


def _config(root: Path, **overrides: object) -> SeedConfig:
    base: dict[str, object] = {
        "identity_url": "http://identity",
        "catalog_url": "http://catalog",
        "media_url": "http://media",
        "game_url": "http://game",
        "admin_login": "admin@example.com",
        "admin_password": "secret",
        "service_secret": "svc-secret",
        "repo_root": root,
        "day": None,
        "poll_interval_s": 0.0,
        "import_timeout_s": 1.0,
        "assets_timeout_s": 1.0,
    }
    from datetime import date

    base["day"] = date(2026, 10, 13)
    base.update(overrides)
    return SeedConfig(**base)  # type: ignore[arg-type]


def _generator(items: list[tuple[str, str, str]]):
    def generate(out: Path) -> None:
        for seed, domain, _ in items:
            ext = ".m4a" if domain == "music" else ".jpg"
            (out / f"{seed.replace(':', '-')}{ext}").write_bytes(
                b"audio-bytes" if domain == "music" else b"frame-bytes"
            )

    return generate


def _run(stack: FakeStack, root: Path, **cfg: object) -> None:
    config = _config(root, **cfg)
    with Seeder(
        config,
        client_factory=stack.client_factory,
        media_generator=_generator(ITEMS),
        echo=lambda _msg: None,
    ) as seeder:
        seeder.run()


class TestSeedHappyPath:
    def test_full_run_makes_expected_calls(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)

        # Аутентификация: сначала сервисный токен, потом вход в админку.
        order = [(r.method, r.url.path) for r in stack.requests]
        assert order[0] == ("POST", "/internal/v1/service-tokens")
        assert order[1] == ("POST", "/internal/v1/admin-sessions")

        # Импорт обоих файлов с Idempotency-Key.
        imports = stack._recorded("POST", "/admin/v1/imports")
        files = {json.loads(r.content)["file_id"] for r in imports}
        assert files == {"films.jsonl", "tracks.jsonl"}
        assert all(r.headers.get("Idempotency-Key") for r in imports)

        # По одному исходнику на единицу с верным видом.
        sources = stack._recorded("POST", "/admin/v1/sources")
        assert len(sources) == len(ITEMS)
        kinds = {r.headers["Idempotency-Key"] for r in sources}
        assert kinds == {"seed-source-film-interstellar", "seed-source-track-bohemian-rhapsody"}

        # Дейлики: только daily-режимы, на заданный день.
        dailies = stack._recorded("POST", r"/admin/v1/daily-sets/.+/regenerate")
        paths = {r.url.path for r in dailies}
        assert paths == {
            f"/admin/v1/daily-sets/film.daily.frame/{DAY}/regenerate",
            f"/admin/v1/daily-sets/music.daily.classic/{DAY}/regenerate",
        }

        # Проекцию game опрашивают ДО перегенерации (ждём играбельный пул, а не media-готовность).
        seq = [
            r
            for r in stack.requests
            if r.url.path == "/admin/v1/projection/items"
            or re.search(r"/daily-sets/.+/regenerate", r.url.path)
        ]
        first_regen = next(i for i, r in enumerate(seq) if "regenerate" in r.url.path)
        assert seq[first_regen - 1].url.path == "/admin/v1/projection/items"

    def test_kind_matches_domain(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        by_key = {
            r.headers["Idempotency-Key"]: r.content
            for r in stack._recorded("POST", "/admin/v1/sources")
        }
        assert b'name="kind"\r\n\r\nframe' in by_key["seed-source-film-interstellar"]
        assert b'name="kind"\r\n\r\naudio' in by_key["seed-source-track-bohemian-rhapsody"]

    def test_rerun_is_idempotent(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        first = len(stack._recorded("POST", "/admin/v1/sources"))
        _run(stack, workspace)  # повторный прогон не падает
        assert len(stack._recorded("POST", "/admin/v1/sources")) == first * 2

    def test_daily_409_treated_as_success(self, workspace: Path) -> None:
        stack = FakeStack(daily_status=409)
        _run(stack, workspace)  # 409 — набор уже есть, это успех


class TestSeedPolling:
    def test_waits_for_assets_ready(self, workspace: Path) -> None:
        stack = FakeStack(assets_ready_after=2)
        _run(stack, workspace)
        polls = [r for r in stack.requests if r.url.path == "/admin/v1/asset-sets"]
        assert len(polls) > len(ITEMS)  # потребовался повторный опрос

    def test_assets_timeout_raises(self, workspace: Path) -> None:
        stack = FakeStack(assets_ready_after=10_000)
        with pytest.raises(SeedError, match="таймаут"):
            _run(stack, workspace, assets_timeout_s=0.02)

    def test_import_failed_raises(self, workspace: Path) -> None:
        stack = FakeStack(import_status="failed")
        with pytest.raises(SeedError, match="ошибкой"):
            _run(stack, workspace)


class TestSeedErrors:
    def test_unexpected_status_reports_code(self, workspace: Path) -> None:
        stack = FakeStack(item_detail_status=503)
        with pytest.raises(SeedError, match="unavailable"):
            _run(stack, workspace)

    def test_missing_source_file_raises(self, workspace: Path) -> None:
        stack = FakeStack()
        config = _config(workspace)
        seeder = Seeder(
            config,
            client_factory=stack.client_factory,
            media_generator=lambda _out: None,  # ничего не сгенерировали
            echo=lambda _msg: None,
        )
        with seeder, pytest.raises(SeedError, match="нет исходника"):
            seeder.run()
