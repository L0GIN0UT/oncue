"""Тесты оркестратора ``gv dev seed`` на фейковом HTTP-стеке (без реального окружения).

Фейк отвечает через ``httpx.MockTransport`` и записывает запросы: проверяем цепочку
аутентификации, использование admin-токена, импорт файлов, загрузку всех видов исходников
с ``position``/``origin=seed``, ожидание наборов, профилей и играбельных пулов, перегенерацию
наборов всех плановых режимов (день и неделя), идемпотентность и опрос готовности.
Отдельно — контракт синтетических сидов ``seeds/catalog/synthetic-*.jsonl`` и имён файлов
генератора ``seeds/media/generate.py``.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from gv_cli.seed import (
    IMPORT_FILES,
    SOURCES_BY_DOMAIN,
    SYNTHETIC_TITLE_QUERY,
    SeedConfig,
    Seeder,
    SeedError,
    mode_plan,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]

# (ключ сида, домен, media_item_id) — согласованы между каталогом, фейком и генератором.
ITEMS = [
    ("film:interstellar", "film", "11111111-1111-1111-1111-111111111111"),
    ("track:bohemian-rhapsody", "music", "22222222-2222-2222-2222-222222222222"),
]
DAY = "2026-10-13"  # вторник
WEEK_START = "2026-10-12"  # понедельник этой недели
FILM_SOURCES = len(SOURCES_BY_DOMAIN["film"])
MUSIC_SOURCES = len(SOURCES_BY_DOMAIN["music"])


@dataclass
class FakeStack:
    """Фейковый стек Guessverse: маршрутизация по пути, запись запросов и настройки готовности."""

    assets_ready_after: int = 0  # сколько опросов asset-sets вернут pending до ready
    failed_clue: str | None = None  # тип загадки, чей набор единицы провален
    import_status: str = "done"  # статус задания импорта в ответе GET
    item_detail_status: int = 200  # для проверки неожиданного ответа каталога
    media_unknown_polls: int = 0  # сколько загрузок ответят 404 (media не знает единицу)
    media_drops: int = 0  # сколько загрузок оборвут соединение (рестарт media-pipeline)
    profiles_pending_polls: int = 0  # сколько опросов профилей вернут pending
    profile_failed: int = 0  # проваленных наборов статического профиля
    daily_status: int = 200  # 409 — набор уже есть
    daily_missing_polls: int = 0  # сколько перегенераций ответят 404 (набора ещё нет)
    daily_items: int | None = None  # единиц в наборе (None — size режима; 0 — пуст)
    daily_set_status: str = "scheduled"  # статус набора дня в ответе/списке
    bracket_subject: str | None = "55555555-5555-5555-5555-555555555555"
    playable_count: int = 40  # сколько играбельных единиц отдаёт поиск проекции (≤ 50)
    synthetic_playable: int = 0  # сколько ещё — только по запросу q=синтетика
    requests: list[httpx.Request] = field(default_factory=list)
    _asset_polls: dict[str, int] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def client_factory(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self.handle))

    def recorded(self, method: str, pattern: str) -> list[httpx.Request]:
        return [
            r for r in self.requests if r.method == method and re.fullmatch(pattern, r.url.path)
        ]

    def _tick(self, name: str) -> int:
        self._counters[name] = self._counters.get(name, 0) + 1
        return self._counters[name]

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
            ("GET", r"/admin/v1/profiles", self._profiles),
            ("GET", r"/admin/v1/projection/items", self._projection),
            ("POST", r"/admin/v1/daily-sets/.+/regenerate", self._daily_post),
            ("GET", r"/admin/v1/daily-sets", self._daily_list),
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

    # ---------------------------------------------------------------- identity
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

    # ---------------------------------------------------------------- catalog
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
        seq = len(self.recorded("POST", "/admin/v1/imports"))
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
                "external_refs": {"seed": seed},
            },
        )

    # ---------------------------------------------------------------- media-pipeline
    def _sources_post(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-source-")
        assert request.headers["content-type"].startswith("multipart/form-data")
        if self._tick("sources_drop") <= self.media_drops:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        if self._tick("sources") <= self.media_unknown_polls:
            return httpx.Response(404, json={"code": "media_item.not_found"})
        fields = form_fields(request)
        assert {"media_item_id", "kind", "position", "origin", "reason"} <= set(fields)
        assert fields["origin"] == "seed"
        assert b'name="file"' in request.content
        return httpx.Response(
            201,
            json={
                "source_media_id": "33333333-3333-3333-3333-333333333333",
                "media_item_id": fields["media_item_id"],
                "kind": fields["kind"],
                "sha256": "x",
                "bytes": 10,
                "mime": "audio/mp4",
                "scheduled_asset_set_ids": [],
                "position": int(fields["position"]),
                "origin": "seed",
            },
        )

    def _asset_sets(self, request: httpx.Request) -> httpx.Response:
        media_item_id = request.url.params["media_item_id"]
        polls = self._asset_polls.get(media_item_id, 0)
        self._asset_polls[media_item_id] = polls + 1
        status = "ready" if polls >= self.assets_ready_after else "pending"
        clue_types = ["audio_intro", "audio_reverse", "emoji"]
        if media_item_id == ITEMS[0][2]:
            clue_types = ["frame_progressive", "frame_sequence", "soundtrack", "description"]
        return httpx.Response(
            200,
            json=[
                {
                    "asset_set_id": f"44444444-4444-4444-4444-44444444444{i}",
                    "media_item_id": media_item_id,
                    "clue_type": clue,
                    "profile_key": "default",
                    "params_hash": "h",
                    # Набор в работе у одного типа: сидер обязан ждать все наборы единицы.
                    "status": "failed"
                    if clue == self.failed_clue
                    else (status if i == len(clue_types) - 1 else "ready"),
                    "levels_count": 6,
                    "generation": 1,
                    "error_code": None,
                }
                for i, clue in enumerate(clue_types)
            ],
        )

    def _profiles(self, request: httpx.Request) -> httpx.Response:
        pending = 1 if self._tick("profiles") <= self.profiles_pending_polls else 0
        profile = {
            "clue_type": "description",
            "profile_key": "default",
            "params_hash": "a" * 64,
            "origin": "static",
            "status": "active",
            "sets": {"ready": 40, "pending": pending, "failed": self.profile_failed},
            "domains": ["film"],
            "error_code": None,
        }
        # Динамический профиль в работе (запрос ассетов из e2e) сидера не задерживает.
        dynamic = {
            **profile,
            "profile_key": None,
            "origin": "dynamic",
            "sets": {"ready": 0, "pending": 5, "failed": 0},
        }
        return httpx.Response(200, json=[profile, dynamic])

    # ---------------------------------------------------------------- game
    def _projection(self, request: httpx.Request) -> httpx.Response:
        # Поиск проекции game: без q — общий список (≤ 50), с q — синтетика отдельно.
        assert request.url.params.get("playable_for_mode")
        assert int(request.url.params["limit"]) <= 50
        q = request.url.params.get("q")
        if q is None:
            ids = [f"01a00000-0000-7000-8000-0000000000{i:02x}" for i in range(self.playable_count)]
        else:
            assert q == SYNTHETIC_TITLE_QUERY
            ids = [
                f"01a00000-0000-7000-9000-0000000000{i:02x}" for i in range(self.synthetic_playable)
            ]
        return httpx.Response(
            200,
            json=[
                {
                    "media_item_id": item_id,
                    "domain": "film",
                    "kind": "film",
                    "title": f"item {item_id}",
                    "year": None,
                    "status": "active",
                    "playable": True,
                }
                for item_id in ids
            ],
        )

    def _period_body(self, mode_key: str, day: str) -> dict[str, object]:
        weekly = mode_key == "film.bracket.weekly"
        count = self.daily_items if self.daily_items is not None else (32 if weekly else 5)
        return {
            "mode_key": mode_key,
            "day": day,
            "mode_version": 1,
            "status": self.daily_set_status,
            "period": "week" if weekly else "day",
            "subject_id": self.bracket_subject if weekly else None,
            "items": [
                {
                    "index": i,
                    "media_item_id": f"01a00000-0000-7000-a000-0000000000{i:02x}",
                    "title": f"set item {i}",
                    "year": None,
                    "playable": True,
                }
                for i in range(count)
            ],
        }

    def _daily_list(self, request: httpx.Request) -> httpx.Response:
        # Список наборов: сидер читает его после 409, чтобы проверить пригодность набора.
        params = request.url.params
        assert params["from"] == params["to"]
        return httpx.Response(200, json=[self._period_body(params["mode_key"], params["from"])])

    def _daily_post(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-period-")
        body = json.loads(request.content)
        assert len(body["reason"]) >= 3
        parts = request.url.path.split("/")
        mode_key, day = parts[4], parts[5]
        if self._tick(f"daily-{mode_key}") <= self.daily_missing_polls:
            return httpx.Response(404, json={"code": "game.daily_set_not_found"})
        if self.daily_status != 200:
            return httpx.Response(self.daily_status, json={"code": "game.daily_locked"})
        return httpx.Response(200, json=self._period_body(mode_key, day))


def form_fields(request: httpx.Request) -> dict[str, str]:
    """Текстовые поля multipart-формы запроса (без файла)."""
    found = re.findall(rb'name="([a-z_]+)"\r\n\r\n([^\r]*)\r\n', request.content)
    return {k.decode(): v.decode() for k, v in found}


_DAILY_YAML = """\
key: {key}
domain: {domain}
source:
  type: daily
  params:
    size: 5
    timezone: Europe/Moscow
    schedule_ahead_days: 2
    pool:
      kinds: [{kind}]
      min_playable: 10
"""

_RANDOM_YAML = """\
key: film.random.description
domain: film
source:
  type: random
  params:
    pool:
      kinds: [film]
      min_playable: 10
"""

_BRACKET_YAML = """\
key: film.bracket.weekly
domain: film
source:
  type: bracket
  params:
    size: 32
    period: week
    timezone: Europe/Moscow
    schedule_ahead_periods: 1
    subject:
      roles: [director]
      min_items: 32
    pool:
      kinds: [film]
"""

_MARATHON_YAML = """\
key: film.marathon.director
domain: film
source:
  type: marathon
  params:
    roles: [director]
    max_subjects: 3
    min_items: 5
    order: year
    pool:
      kinds: [film]
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
        _DAILY_YAML.format(key="film.daily.frame", domain="film", kind="film"), encoding="utf-8"
    )
    (modes / "music.daily.classic.yaml").write_text(
        _DAILY_YAML.format(key="music.daily.classic", domain="music", kind="track"),
        encoding="utf-8",
    )
    (modes / "film.random.description.yaml").write_text(_RANDOM_YAML, encoding="utf-8")
    (modes / "film.bracket.weekly.yaml").write_text(_BRACKET_YAML, encoding="utf-8")
    (modes / "film.marathon.director.yaml").write_text(_MARATHON_YAML, encoding="utf-8")
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
        "day": date.fromisoformat(DAY),
        "poll_interval_s": 0.0,
        "import_timeout_s": 1.0,
        "assets_timeout_s": 1.0,
    }
    base.update(overrides)
    return SeedConfig(**base)  # type: ignore[arg-type]


def _generator(items: list[tuple[str, str, str]]) -> Callable[[Path], None]:
    def generate(out: Path) -> None:
        for seed, domain, _ in items:
            for spec in SOURCES_BY_DOMAIN[domain]:
                spec.path(out, seed).write_bytes(f"{spec.kind}-{spec.position}".encode())

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

        # Импорт имеющихся файлов с Idempotency-Key.
        imports = stack.recorded("POST", "/admin/v1/imports")
        files = {json.loads(r.content)["file_id"] for r in imports}
        assert files == {"films.jsonl", "tracks.jsonl"}
        assert all(r.headers.get("Idempotency-Key") for r in imports)

        # Все виды исходников на единицу, у каждого свой ключ идемпотентности.
        sources = stack.recorded("POST", "/admin/v1/sources")
        assert len(sources) == FILM_SOURCES + MUSIC_SOURCES
        keys = [r.headers["Idempotency-Key"] for r in sources]
        assert len(set(keys)) == len(keys)

        # Плановые режимы: дейлики — на день, сетка — на понедельник недели; прочие — нет.
        regen = stack.recorded("POST", r"/admin/v1/daily-sets/.+/regenerate")
        assert {r.url.path for r in regen} == {
            f"/admin/v1/daily-sets/film.daily.frame/{DAY}/regenerate",
            f"/admin/v1/daily-sets/music.daily.classic/{DAY}/regenerate",
            f"/admin/v1/daily-sets/film.bracket.weekly/{WEEK_START}/regenerate",
        }

        # Пулы ждут для каждого режима (и неплановых тоже), до любой перегенерации.
        polled = {
            r.url.params["playable_for_mode"]
            for r in stack.recorded("GET", "/admin/v1/projection/items")
        }
        assert polled == {
            "film.daily.frame",
            "music.daily.classic",
            "film.random.description",
            "film.bracket.weekly",
            "film.marathon.director",
        }
        paths = [r.url.path for r in stack.requests]
        first_regen = next(i for i, p in enumerate(paths) if p.endswith("/regenerate"))
        last_projection = max(i for i, p in enumerate(paths) if p == "/admin/v1/projection/items")
        assert last_projection < first_regen
        # Профили media ждут после загрузок.
        assert paths.index("/admin/v1/profiles") > paths.index("/admin/v1/sources")

    def test_sources_carry_kind_position_and_origin(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        by_item: dict[str, list[tuple[str, int]]] = {}
        for r in stack.recorded("POST", "/admin/v1/sources"):
            f = form_fields(r)
            by_item.setdefault(f["media_item_id"], []).append((f["kind"], int(f["position"])))
        film, track = ITEMS[0][2], ITEMS[1][2]
        assert by_item[track] == [("audio", 1)]
        # Шесть кадров frame_sequence по позициям 1..6, затем саундтрек и звук фильма.
        assert by_item[film] == [
            *(("frame", n) for n in range(1, 7)),
            ("soundtrack", 1),
            ("audio_clip", 1),
        ]

    def test_rerun_is_idempotent(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        first = [r.headers["Idempotency-Key"] for r in stack.recorded("POST", "/admin/v1/sources")]
        _run(stack, workspace)  # повторный прогон не падает и шлёт те же ключи
        keys = [r.headers["Idempotency-Key"] for r in stack.recorded("POST", "/admin/v1/sources")]
        assert keys == first * 2

    def test_period_409_treated_as_success(self, workspace: Path) -> None:
        stack = FakeStack(daily_status=409)
        _run(stack, workspace)  # 409 — набор уже есть, это успех
        # После 409 сидер читает список наборов, в том числе недельный — на начало недели.
        listed = {
            (r.url.params["mode_key"], r.url.params["from"])
            for r in stack.recorded("GET", "/admin/v1/daily-sets")
        }
        assert ("film.bracket.weekly", WEEK_START) in listed
        assert ("film.daily.frame", DAY) in listed

    def test_waits_for_period_set_created_by_scheduler(self, workspace: Path) -> None:
        # Набор периода создаёт задача game: пока его нет (404), сидер ждёт.
        stack = FakeStack(daily_missing_polls=2)
        _run(stack, workspace)
        weekly = stack.recorded("POST", r"/admin/v1/daily-sets/film\.bracket\.weekly/.+")
        assert len(weekly) == 3

    def test_waits_until_media_knows_item(self, workspace: Path) -> None:
        # media-pipeline ещё не обработал MediaItemCreated: 404 на загрузке — ждать.
        stack = FakeStack(media_unknown_polls=2)
        _run(stack, workspace)
        assert len(stack.recorded("POST", "/admin/v1/sources")) == (
            FILM_SOURCES + MUSIC_SOURCES + 2
        )

    def test_retries_upload_when_media_restarts(self, workspace: Path) -> None:
        # Регресс засева 3a: рестарт media-pipeline рвал соединение посреди загрузки и ронял
        # засев. Загрузка идемпотентна по ключу — повторяем с тем же ключом.
        stack = FakeStack(media_drops=2)
        _run(stack, workspace)
        keys = [r.headers["Idempotency-Key"] for r in stack.recorded("POST", "/admin/v1/sources")]
        assert len(keys) == FILM_SOURCES + MUSIC_SOURCES + 2
        assert keys[0] == keys[1] == keys[2]


class TestPeriodSetValidation:
    def test_empty_daily_set_raises(self, workspace: Path) -> None:
        # Набор без единиц → сессия упала бы с 503; сидер обязан упасть здесь.
        stack = FakeStack(daily_items=0)
        with pytest.raises(SeedError, match="непригоден"):
            _run(stack, workspace)

    def test_failed_daily_set_raises(self, workspace: Path) -> None:
        # Статус failed (пул меньше нужного) обязан падать на этапе сидирования.
        stack = FakeStack(daily_set_status="failed")
        with pytest.raises(SeedError, match="непригоден"):
            _run(stack, workspace)

    def test_daily_409_with_failed_set_raises(self, workspace: Path) -> None:
        # 409 не должен маскировать непригодный набор, созданный планировщиком.
        stack = FakeStack(daily_status=409, daily_items=0)
        with pytest.raises(SeedError, match="непригоден"):
            _run(stack, workspace)

    def test_bracket_without_theme_raises(self, workspace: Path) -> None:
        stack = FakeStack(bracket_subject=None)
        with pytest.raises(SeedError, match=r"film\.bracket\.weekly.*subject_id"):
            _run(stack, workspace)

    def test_short_set_raises(self, workspace: Path) -> None:
        # Сетке нужно ровно size единиц: 5 из 32 — непригодна.
        stack = FakeStack(daily_items=5)
        with pytest.raises(SeedError, match="5 вместо 32"):
            _run(stack, workspace)


class TestSeedPolling:
    def test_waits_for_all_asset_sets_ready(self, workspace: Path) -> None:
        stack = FakeStack(assets_ready_after=2)
        _run(stack, workspace)
        polls = stack.recorded("GET", "/admin/v1/asset-sets")
        assert len(polls) == 3 * len(ITEMS)  # два pending-опроса на единицу + ready

    def test_assets_timeout_raises(self, workspace: Path) -> None:
        stack = FakeStack(assets_ready_after=10_000)
        with pytest.raises(SeedError, match="таймаут"):
            _run(stack, workspace, assets_timeout_s=0.02)

    def test_failed_asset_set_names_clue_type(self, workspace: Path) -> None:
        stack = FakeStack(failed_clue="frame_sequence")
        with pytest.raises(SeedError, match="frame_sequence/default"):
            _run(stack, workspace)

    def test_waits_for_static_profiles(self, workspace: Path) -> None:
        stack = FakeStack(profiles_pending_polls=2)
        _run(stack, workspace)
        assert len(stack.recorded("GET", "/admin/v1/profiles")) == 3

    def test_failed_profile_raises(self, workspace: Path) -> None:
        stack = FakeStack(profile_failed=1)
        with pytest.raises(SeedError, match="description/default"):
            _run(stack, workspace)

    def test_synthetic_items_count_towards_pool(self, workspace: Path) -> None:
        # Общий поиск отдаёт 50 строк по названию: синтетика может в них не попасть —
        # сидер спрашивает её отдельно и складывает играбельные единицы обоих запросов.
        stack = FakeStack(playable_count=10, synthetic_playable=30)
        _run(stack, workspace)  # сетке нужно 32: 10 + 30 ≥ 32

    def test_pool_timeout_reports_counts(self, workspace: Path) -> None:
        stack = FakeStack(playable_count=10, synthetic_playable=0)
        with pytest.raises(SeedError, match=r"film\.bracket\.weekly.*10 из 32"):
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


class TestModePlan:
    @pytest.mark.parametrize(
        ("key", "min_playable", "period", "size", "themed"),
        [
            ("music.daily.classic", 10, "day", 5, False),
            ("film.bracket.weekly", 32, "week", 32, True),
            ("film.marathon.director", 5, None, None, False),
            ("film.pair.rating", 10, None, None, False),
            ("music.random.reverse", 20, None, None, False),
        ],
    )
    def test_real_seeds(
        self, key: str, min_playable: int, period: str | None, size: int | None, themed: bool
    ) -> None:
        import yaml

        spec = yaml.safe_load((REPO / "seeds/game/modes" / f"{key}.yaml").read_text("utf-8"))
        plan = mode_plan(spec)
        assert (plan.min_playable, plan.period, plan.size, plan.themed) == (
            min_playable,
            period,
            size,
            themed,
        )

    def test_week_starts_on_monday(self) -> None:
        plan = mode_plan({"key": "k", "source": {"params": {"period": "week", **_AHEAD}}})
        assert plan.period_start(date(2026, 10, 18)) == date(2026, 10, 12)  # воскресенье
        assert plan.period_start(date(2026, 10, 12)) == date(2026, 10, 12)


_AHEAD = {"schedule_ahead_periods": 1}


# ----------------------------------------------------------------------------- синтетика
def _rows(name: str) -> list[dict[str, Any]]:
    lines = (REPO / "seeds" / "catalog" / name).read_text("utf-8").splitlines()
    return [json.loads(x) for x in lines if x.strip() and not x.startswith("#")]


class TestSyntheticCatalog:
    """Контракт ``seeds/catalog/synthetic-*.jsonl`` для режимов этапа 3a (план §10)."""

    def test_files_are_imported(self) -> None:
        assert {"synthetic-films.jsonl", "synthetic-tracks.jsonl"} <= set(IMPORT_FILES)

    @pytest.mark.parametrize(
        ("name", "role", "metrics"),
        [
            ("synthetic-films.jsonl", "director", ("vote_average", "popularity")),
            ("synthetic-tracks.jsonl", "artist", ("popularity",)),
        ],
    )
    def test_one_person_and_no_metric_ties(
        self, name: str, role: str, metrics: tuple[str, ...]
    ) -> None:
        rows = _rows(name)
        # Сетке нужно ≥ 32 играбельных единиц одной персоны, марафону — ≥ 5.
        assert len(rows) >= 40
        keys = {p["key"] for r in rows for p in r["persons"] if p["role"] == role}
        assert len(keys) == 1
        assert all(r["title"].startswith(SYNTHETIC_TITLE_QUERY) for r in rows)
        assert len({r["title"] for r in rows}) == len(rows)
        assert len({r["key"] for r in rows}) == len(rows)
        # Порядок марафона по году и «больше/меньше»: без ничьих ни по одной метрике пары.
        assert len({r["year"] for r in rows}) == len(rows)
        for metric in metrics:
            assert len({r["metrics"][metric] for r in rows}) == len(rows), metric

    def test_films_satisfy_description_profile(self) -> None:
        # Профиль description/default: tagline, сюжет, год+жанры, страна, режиссёр, актёры.
        for row in _rows("synthetic-films.jsonl"):
            texts = row["clue_texts"]
            assert texts["tagline"] and texts["plot_masked"], row["key"]
            assert row["year"] and row["genres"] and row["countries"], row["key"]
            roles = [p["role"] for p in row["persons"]]
            assert "director" in roles and "actor" in roles, row["key"]
            # Название в сюжете раскрыло бы ответ.
            assert row["title"].split(": ", 1)[-1].lower() not in texts["plot_masked"].lower()

    @pytest.mark.parametrize("name", ["synthetic-films.jsonl", "synthetic-tracks.jsonl"])
    def test_emoji_cover_all_levels(self, name: str) -> None:
        # Профиль emoji/default открывает до 6 эмодзи; catalog принимает ≤ 12 графем.
        for row in _rows(name):
            emoji = row["clue_texts"]["emoji"]
            assert 6 <= len(emoji) <= 12, row["key"]
            assert not any(ch.isspace() or ch.isalnum() for ch in emoji), row["key"]

    def test_no_overlap_with_stage2_seeds(self) -> None:
        synthetic = {
            r["key"] for n in ("synthetic-films.jsonl", "synthetic-tracks.jsonl") for r in _rows(n)
        }
        stage2 = {r["key"] for n in ("films.jsonl", "tracks.jsonl") for r in _rows(n)}
        assert not synthetic & stage2


# ----------------------------------------------------------------------------- генератор
def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "seed_media_generate", REPO / "seeds/media/generate.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMediaGenerator:
    def test_film_audio_long_enough(self) -> None:
        gen = _load_generator()
        # Динамические профили (запрос ассетов) могут начинать отрывок позже: ≥ 45 с.
        assert gen.FILM_AUDIO_SECONDS >= 45
        assert gen.TONE_SECONDS >= 45

    def test_names_match_seeder(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        gen = _load_generator()

        def fake_audio(expr: str, seconds: int, path: Path) -> Path:
            path.write_bytes(b"aac")
            return path

        monkeypatch.setattr(gen, "_ffmpeg_audio", fake_audio)
        catalog = tmp_path / "catalog"
        catalog.mkdir()
        (catalog / "x.jsonl").write_text(
            '{"key": "film:synthetic-01", "kind": "film", "title": "f"}\n'
            '{"key": "track:synthetic-01", "kind": "track", "title": "t"}\n',
            encoding="utf-8",
        )
        out = tmp_path / "out"
        monkeypatch.setattr(
            "sys.argv", ["generate.py", "--out", str(out), "--catalog", str(catalog)]
        )
        gen.main()
        for key, domain in (("film:synthetic-01", "film"), ("track:synthetic-01", "music")):
            for source in SOURCES_BY_DOMAIN[domain]:
                assert source.path(out, key).is_file(), source
