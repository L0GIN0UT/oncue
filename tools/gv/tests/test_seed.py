"""Тесты оркестратора ``gv dev seed`` на фейковом HTTP-стеке (без реального окружения).

Фейк отвечает через ``httpx.MockTransport`` и записывает запросы: проверяем цепочку
аутентификации, использование admin-токена, импорт файлов, загрузку всех видов исходников
с ``position``/``origin=seed``, ожидание наборов, профилей и играбельных пулов, перегенерацию
наборов всех плановых режимов (день и неделя), идемпотентность и опрос готовности.
Кураторские сборники 3b: создание/новая версия через admin-API collections, ожидание
единиц в проекции collections, наборов своих эмодзи со ``scope`` и проекции сборника в game.
Отдельно — контракт синтетических сидов ``seeds/catalog/synthetic-*.jsonl``, сидов сборников
``seeds/collections/*.yaml`` и имён файлов генератора ``seeds/media/generate.py``.
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
    ITEM_UNAVAILABLE,
    SOURCES_BY_DOMAIN,
    SYNTHETIC_TITLE_QUERY,
    SeedConfig,
    Seeder,
    SeedError,
    load_collection_spec,
    lobby_collections,
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
    sources_key_reused: bool = False  # повторный засев: те же ключи, другие байты → 409
    sources_present: bool = True  # GET /admin/v1/sources отдаёт уже загруженные исходники
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
    playable_items: tuple[str, ...] = ()  # media_item_id единиц ITEMS, играбельных в поиске
    unpublished: str | None = None  # режим, которого нет среди опубликованных (404)
    # game: онлайн-шаблоны в GET /v1/modes?session=online и сколько опросов их ещё нет
    online_modes: tuple[str, ...] = ()
    online_missing_polls: int = 0
    # collections: сколько мутаций ответят 422 item_unavailable (проекция не догнала catalog)
    collection_unknown_polls: int = 0
    collection_drops: int = 0  # сколько мутаций оборвут соединение (рестарт collections)
    collection_error: str | None = None  # код нарушения публикации (иначе — успех)
    hidden: bool = False  # существующие сборники скрыты модератором
    # game: сколько опросов режимов сборника ответят 404 (CollectionPublished не обработан)
    modes_missing_polls: int = 0
    modes_short: int = 0  # сколько единиц сборника game не считает играбельными
    scoped_pending_polls: int = 0  # опросов наборов со scope в статусе pending
    scoped_failed: bool = False  # набор своих эмодзи провален
    collections: dict[str, dict[str, Any]] = field(default_factory=dict)
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
            ("GET", r"/admin/v1/sources", self._sources_list),
            ("GET", r"/admin/v1/asset-sets", self._asset_sets),
            ("GET", r"/admin/v1/profiles", self._profiles),
            ("GET", r"/admin/v1/projection/items", self._projection),
            ("POST", r"/admin/v1/daily-sets/.+/regenerate", self._daily_post),
            ("GET", r"/admin/v1/daily-sets", self._daily_list),
            ("GET", r"/admin/v1/collections", self._collections_list),
            ("POST", r"/admin/v1/collections", self._collection_create),
            ("GET", rf"/admin/v1/collections/{_UUID}", self._collection_detail),
            ("PUT", rf"/admin/v1/collections/{_UUID}", self._collection_update),
            ("PUT", rf"/admin/v1/collections/{_UUID}/featured", self._collection_featured),
            ("GET", rf"/v1/collections/{_UUID}/modes", self._collection_modes),
            ("GET", r"/v1/modes", self._public_modes),
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        # Все admin-ручки обязаны нести admin-токен, публичные — не нести его.
        if path.startswith("/admin/v1/"):
            assert request.headers["Authorization"] == "Bearer admin-tok", path
        if path.startswith("/v1/"):
            assert "Authorization" not in request.headers, path
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
    def _sources_list(self, request: httpx.Request) -> httpx.Response:
        if not self.sources_present:
            return httpx.Response(200, json=[])
        every = [("audio", 1), ("soundtrack", 1), ("audio_clip", 1)]
        every += [("frame", n) for n in range(1, 7)]
        return httpx.Response(200, json=[{"kind": k, "position": p} for k, p in every])

    def _sources_post(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-source-")
        if self.sources_key_reused:
            return httpx.Response(409, json={"code": "idempotency.key_reused"})
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
        sets: list[dict[str, Any]] = [
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
                "scope": None,
            }
            for i, clue in enumerate(clue_types)
        ]
        return httpx.Response(200, json=sets + self._scoped_sets(media_item_id))

    def _scoped_sets(self, media_item_id: str) -> list[dict[str, Any]]:
        """Наборы своих эмодзи сборников (media строит их из CollectionPublished)."""
        sets: list[dict[str, Any]] = []
        for collection_id, detail in self.collections.items():
            for item in detail["items"]:
                if item["media_item_id"] != media_item_id or not item["custom"]:
                    continue
                polls = self._tick(f"scoped-{collection_id}-{media_item_id}")
                status = "pending" if polls <= self.scoped_pending_polls else "ready"
                sets.append(
                    {
                        "asset_set_id": "55555555-5555-5555-5555-555555555555",
                        "media_item_id": media_item_id,
                        "clue_type": "emoji",
                        "profile_key": "default",
                        "params_hash": "h",
                        "status": "failed" if self.scoped_failed else status,
                        "levels_count": 6,
                        "generation": 1,
                        "error_code": "emoji.too_short" if self.scoped_failed else None,
                        "scope": {"kind": "collection", "id": collection_id},
                    }
                )
        return sets

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
        if request.url.params["playable_for_mode"] == self.unpublished:
            return _problem(404, "game.mode_not_found")
        q = request.url.params.get("q")
        if q is None:
            ids = [f"01a00000-0000-7000-8000-0000000000{i:02x}" for i in range(self.playable_count)]
            ids += list(self.playable_items)
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

    # ---------------------------------------------------------------- collections
    def _collections_list(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["kind"] == "curated"
        assert int(params["limit"]) <= 100
        rows = [
            {k: v for k, v in d.items() if k not in {"items", "tags", "cover", "description"}}
            for d in self.collections.values()
            if params["q"].lower() in d["title"].lower()
        ]
        return httpx.Response(200, json={"items": rows, "next_cursor": None})

    def _collection_detail(self, request: httpx.Request) -> httpx.Response:
        collection_id = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=self.collections[collection_id])

    def _mutation_failure(self, request: httpx.Request) -> httpx.Response | None:
        """Общие отказы мутаций: обрыв, проекция не догнала, нарушение публикации."""
        assert request.headers["Idempotency-Key"].startswith("seed-collection-")
        assert len(json.loads(request.content)["reason"]) >= 3
        if self._tick("collections_drop") <= self.collection_drops:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        if self._tick("collections_unknown") <= self.collection_unknown_polls:
            return _problem(
                422,
                "collections.publish_invalid",
                errors=[{"path": "items[0]", "code": ITEM_UNAVAILABLE}],
            )
        if self.collection_error:
            return _problem(
                422,
                "collections.publish_invalid",
                errors=[{"path": "items[0].custom.emoji", "code": self.collection_error}],
            )
        return None

    def _store(self, collection_id: str, body: dict[str, Any], version: int) -> dict[str, Any]:
        detail = {
            "collection_id": collection_id,
            "domain": body.get("domain", self.collections.get(collection_id, {}).get("domain")),
            "kind": "curated",
            "status": "hidden" if self.hidden else "published",
            "visibility": body["visibility"],
            "title": body["title"],
            "description": body["description"],
            "tags": body["tags"],
            "cover": body["cover"],
            "default_mode_key": body["default_mode_key"],
            "version": version,
            "featured": False,
            "featured_rank": None,
            "items": [
                {
                    "position": pos,
                    "media_item_id": item["media_item_id"],
                    "title": "t",
                    "year": None,
                    "available": True,
                    "custom": item["custom"],
                }
                for pos, item in enumerate(body["items"])
            ],
        }
        self.collections[collection_id] = detail
        return detail

    def _collection_create(self, request: httpx.Request) -> httpx.Response:
        failure = self._mutation_failure(request)
        if failure is not None:
            return failure
        body = json.loads(request.content)
        assert body["domain"] in {"film", "music"}
        seq = len(self.collections) + 1
        collection_id = f"66666666-6666-6666-6666-00000000000{seq}"
        return httpx.Response(201, json=self._store(collection_id, body, 1))

    def _collection_update(self, request: httpx.Request) -> httpx.Response:
        failure = self._mutation_failure(request)
        if failure is not None:
            return failure
        collection_id = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        current = self.collections[collection_id]["version"]
        if body["base_version"] != current:
            return _problem(409, "collections.version_conflict")
        return httpx.Response(200, json=self._store(collection_id, body, current + 1))

    def _collection_featured(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"].startswith("seed-featured-")
        collection_id = request.url.path.split("/")[-2]
        body = json.loads(request.content)
        detail = self.collections[collection_id]
        detail.update(featured=body["featured"], featured_rank=body["rank"])
        return httpx.Response(200, json=detail)

    def _collection_modes(self, request: httpx.Request) -> httpx.Response:
        collection_id = request.url.path.split("/")[-2]
        if self._tick(f"modes-{collection_id}") <= self.modes_missing_polls:
            return _problem(404, "game.collection_not_found")
        detail = self.collections[collection_id]
        total = len(detail["items"])
        playable = max(total - self.modes_short, 0)
        return httpx.Response(
            200,
            json=[
                {
                    "mode_key": detail["default_mode_key"],
                    "title_key": "t",
                    "clue_type": "frame_progressive",
                    "allow_guest": True,
                    "playable": playable,
                    "total": total,
                    "available": playable >= 1,
                    "round_options": [5],
                }
            ],
        )

    def _public_modes(self, request: httpx.Request) -> httpx.Response:
        # Онлайн-шаблоны: публичный каталог game без admin-токена.
        assert request.url.params["session"] == "online"
        if self._tick("online_modes") <= self.online_missing_polls:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200, json=[{"key": key, "domain": key.split(".")[0]} for key in self.online_modes]
        )


def _problem(status: int, code: str, **details: Any) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "status": status, "details": details or None})


_UUID = "[0-9a-f-]{36}"


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
    collections = tmp_path / "seeds" / "collections"
    collections.mkdir(parents=True)
    (collections / "film-emoji.yaml").write_text(_FILM_COLLECTION, encoding="utf-8")
    (collections / "music-intro.yaml").write_text(_MUSIC_COLLECTION, encoding="utf-8")
    return tmp_path


_FILM_COLLECTION = """\
key: film-emoji
title: "Кино · эмодзи"
description: "Фильмы в эмодзи"
domain: film
default_mode_key: film.collection.emoji
tags: [scifi]
cover: popcorn
featured_rank: 1
items:
  - item: film:interstellar
    emoji: "🚀🌌⏳🌽🪐📚"
"""

_MUSIC_COLLECTION = """\
key: music-intro
title: "Музыка · интро"
domain: music
default_mode_key: music.collection.intro
items:
  - track:bohemian-rhapsody
"""


def _config(root: Path, **overrides: object) -> SeedConfig:
    base: dict[str, object] = {
        "identity_url": "http://identity",
        "catalog_url": "http://catalog",
        "media_url": "http://media",
        "game_url": "http://game",
        "collections_url": "http://collections",
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

    def test_repeated_seed_counts_uploaded_sources(self, workspace: Path) -> None:
        # Повторный засев: ffmpeg дал другие байты → 409 key_reused; исходники уже есть → ок.
        stack = FakeStack(sources_key_reused=True)
        _run(stack, workspace)
        assert stack.recorded("GET", "/admin/v1/sources")

    def test_key_reused_without_uploaded_source_fails(self, workspace: Path) -> None:
        stack = FakeStack(sources_key_reused=True, sources_present=False)
        with pytest.raises(SeedError, match="409"):
            _run(stack, workspace)


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
        # Шаг исходников — до сборников (те опрашивают asset-sets ради своих эмодзи).
        first_collection = next(
            i for i, r in enumerate(stack.requests) if r.url.path == "/admin/v1/collections"
        )
        polls = [
            r for r in stack.requests[:first_collection] if r.url.path == "/admin/v1/asset-sets"
        ]
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


FILM_ID, TRACK_ID = ITEMS[0][2], ITEMS[1][2]
_MUTATIONS = r"/admin/v1/collections(/[0-9a-f-]{36})?"


def _mutations(stack: FakeStack) -> list[httpx.Request]:
    return [
        r
        for r in stack.requests
        if r.method in {"POST", "PUT"} and re.fullmatch(_MUTATIONS, r.url.path)
    ]


class TestSeedCollections:
    def test_creates_curated_collections_through_admin_api(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)

        created = stack.recorded("POST", "/admin/v1/collections")
        assert len(created) == 2
        bodies = {json.loads(r.content)["title"]: json.loads(r.content) for r in created}
        film = bodies["Кино · эмодзи"]
        # Ключи сидов → media_item_id каталога, свои эмодзи — в custom, словари — как в сиде.
        assert film["domain"] == "film"
        assert film["default_mode_key"] == "film.collection.emoji"
        assert film["items"] == [{"media_item_id": FILM_ID, "custom": {"emoji": "🚀🌌⏳🌽🪐📚"}}]
        assert film["tags"] == ["scifi"]
        assert film["cover"] == {"preset": "popcorn"}
        assert len(film["reason"]) >= 3
        music = bodies["Музыка · интро"]
        assert music["items"] == [{"media_item_id": TRACK_ID, "custom": None}]
        assert music["cover"] is None
        # Ключ идемпотентности стабилен и свой у каждого сборника.
        keys = [r.headers["Idempotency-Key"] for r in created]
        assert len(set(keys)) == 2
        assert all(k.startswith("seed-collection-") for k in keys)

        # «Рекомендуемое» — только у сборника с featured_rank.
        featured = stack.recorded("PUT", rf"/admin/v1/collections/{_UUID}/featured")
        assert len(featured) == 1
        assert json.loads(featured[0].content) == {
            "featured": True,
            "rank": 1,
            "reason": "сиды e2e",
        }

        # Сборники — после наборов периода; game опрашивается публично по каждому сборнику.
        paths = [r.url.path for r in stack.requests]
        last_regen = max(i for i, p in enumerate(paths) if p.endswith("/regenerate"))
        assert paths.index("/admin/v1/collections") > last_regen
        modes = {r.url.path for r in stack.recorded("GET", rf"/v1/collections/{_UUID}/modes")}
        assert modes == {f"/v1/collections/{cid}/modes" for cid in stack.collections}

    def test_rerun_without_changes_does_not_mutate(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        _run(stack, workspace)
        assert len(_mutations(stack)) == 2  # только создание при первом прогоне
        assert len(stack.recorded("PUT", rf"/admin/v1/collections/{_UUID}/featured")) == 1

    def test_changed_seed_publishes_new_version(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        path = workspace / "seeds" / "collections" / "music-intro.yaml"
        path.write_text(_MUSIC_COLLECTION + "tags: [rock]\n", encoding="utf-8")
        _run(stack, workspace)

        updates = stack.recorded("PUT", rf"/admin/v1/collections/{_UUID}")
        assert len(updates) == 1
        body = json.loads(updates[0].content)
        assert body["base_version"] == 1
        assert body["tags"] == ["rock"]
        assert "domain" not in body
        created_keys = {r.headers["Idempotency-Key"] for r in stack.recorded("POST", _MUTATIONS)}
        assert updates[0].headers["Idempotency-Key"] not in created_keys
        assert {d["version"] for d in stack.collections.values()} == {1, 2}

    def test_waits_until_collections_knows_items(self, workspace: Path) -> None:
        # collections ещё не обработал MediaItemCreated: 422 item_unavailable — ждать.
        stack = FakeStack(collection_unknown_polls=2)
        _run(stack, workspace)
        keys = [r.headers["Idempotency-Key"] for r in _mutations(stack)]
        assert len(keys) == 4
        assert keys[0] == keys[1] == keys[2]

    def test_retries_when_collections_restarts(self, workspace: Path) -> None:
        stack = FakeStack(collection_drops=1)
        _run(stack, workspace)
        keys = [r.headers["Idempotency-Key"] for r in _mutations(stack)]
        assert len(keys) == 3
        assert keys[0] == keys[1]

    def test_publish_violation_raises_with_codes(self, workspace: Path) -> None:
        stack = FakeStack(collection_error="collections.emoji_too_short")
        with pytest.raises(
            SeedError, match=r"items\[0\]\.custom\.emoji: collections\.emoji_too_short"
        ):
            _run(stack, workspace)
        assert len(_mutations(stack)) == 1  # без повторов

    def test_items_unavailable_timeout_names_paths(self, workspace: Path) -> None:
        stack = FakeStack(collection_unknown_polls=10_000)
        with pytest.raises(SeedError, match=r"проекции collections.*items\[0\]"):
            _run(stack, workspace, assets_timeout_s=0.02)

    def test_waits_for_scoped_emoji_sets(self, workspace: Path) -> None:
        stack = FakeStack(scoped_pending_polls=2)
        _run(stack, workspace)
        # После создания сборника — опросы наборов единицы со своими эмодзи до ready.
        first = next(
            i for i, r in enumerate(stack.requests) if r.url.path == "/admin/v1/collections"
        )
        polls = [
            r
            for r in stack.requests[first:]
            if r.url.path == "/admin/v1/asset-sets" and r.url.params["media_item_id"] == FILM_ID
        ]
        assert len(polls) == 3
        # Музыкальный сборник без своих эмодзи наборы со scope не ждёт.
        assert all(
            r.url.params["media_item_id"] != TRACK_ID
            for r in stack.requests[first:]
            if r.url.path == "/admin/v1/asset-sets"
        )

    def test_failed_scoped_set_raises(self, workspace: Path) -> None:
        stack = FakeStack(scoped_failed=True)
        with pytest.raises(SeedError, match=r"film-emoji/film:interstellar.*failed"):
            _run(stack, workspace)

    def test_waits_for_collection_in_game(self, workspace: Path) -> None:
        stack = FakeStack(modes_missing_polls=2)
        _run(stack, workspace)
        for collection_id in stack.collections:
            polls = stack.recorded("GET", rf"/v1/collections/{collection_id}/modes")
            assert len(polls) == 3

    def test_game_timeout_reports_playable(self, workspace: Path) -> None:
        stack = FakeStack(modes_short=1)
        with pytest.raises(SeedError, match=r"film-emoji в проекции game.*играбельно 0 из 1"):
            _run(stack, workspace, assets_timeout_s=0.02)

    def test_hidden_collection_raises(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        stack.hidden = True
        for detail in stack.collections.values():
            detail["status"] = "hidden"
        with pytest.raises(SeedError, match="скрыт модератором"):
            _run(stack, workspace)

    def test_unknown_item_key_fails_before_import(self, workspace: Path) -> None:
        path = workspace / "seeds" / "collections" / "music-intro.yaml"
        path.write_text(_MUSIC_COLLECTION + "  - track:missing\n", encoding="utf-8")
        stack = FakeStack()
        with pytest.raises(SeedError, match=r"track:missing"):
            _run(stack, workspace)
        assert not stack.recorded("POST", "/admin/v1/imports")

    def test_bad_seed_names_file(self, workspace: Path) -> None:
        path = workspace / "seeds" / "collections" / "broken.yaml"
        path.write_text("key: broken\ntitle: x\n", encoding="utf-8")
        with pytest.raises(SeedError, match=r"broken\.yaml"):
            _run(FakeStack(), workspace)

    def test_no_collections_dir_skips_step(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace, collections_dir=workspace / "nowhere")
        assert not stack.recorded("GET", "/admin/v1/collections")


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

    @pytest.mark.parametrize(
        ("key", "min_playable", "rounds", "lobby_by_collection"),
        [
            # Пул шаблона больше раундов матча — берётся пул.
            ("music.duel.classic", 20, 7, False),
            ("film.duel.frame", 20, 7, False),
            ("film.party.frame", 20, 10, False),
            ("music.lobby.classic", 20, 10, False),
            # Лобби по сборнику: min_items 5, но матч разыгрывает session.rounds = 12 сразу.
            ("film.lobby.collection", 12, 12, True),
        ],
    )
    def test_online_templates(
        self, key: str, min_playable: int, rounds: int, lobby_by_collection: bool
    ) -> None:
        import yaml

        spec = yaml.safe_load((REPO / "seeds/game/modes" / f"{key}.yaml").read_text("utf-8"))
        plan = mode_plan(spec)
        assert plan.online
        assert plan.period is None
        assert (plan.min_playable, plan.rounds, plan.lobby_by_collection) == (
            min_playable,
            rounds,
            lobby_by_collection,
        )
        assert plan.domain == key.split(".", maxsplit=1)[0]

    def test_solo_modes_are_not_online(self) -> None:
        import yaml

        for path in sorted((REPO / "seeds/game/modes").glob("*.yaml")):
            spec = yaml.safe_load(path.read_text("utf-8"))
            kind = (spec.get("session") or {}).get("kind", "solo")
            assert mode_plan(spec).online is (kind != "solo"), path.name


_AHEAD = {"schedule_ahead_periods": 1}


# ----------------------------------------------------------------------------- онлайн 4a
_DUEL_YAML = """\
key: music.duel.classic
domain: music
source:
  type: random
  params:
    pool:
      kinds: [track]
      min_playable: 1
session:
  kind: duel
  rounds: 1
"""

_LOBBY_YAML = """\
key: film.lobby.collection
domain: film
source:
  type: collection
  params:
    min_items: 1
session:
  kind: lobby
  rounds: {rounds}
"""

_LOBBY_COLLECTION = """\
key: film-lobby
title: "Кино · лобби"
domain: film
default_mode_key: film.collection.frame
items:
  - film:interstellar
"""

ONLINE = ("music.duel.classic", "film.lobby.collection")
FILM_ID = ITEMS[0][2]


def _online(workspace: Path, *, lobby_rounds: int = 1) -> Path:
    """Добавить в рабочий каталог онлайн-шаблоны (дуэль, лобби по сборнику) и сборник лобби."""
    modes = workspace / "seeds" / "game" / "modes"
    (modes / "music.duel.classic.yaml").write_text(_DUEL_YAML, encoding="utf-8")
    (modes / "film.lobby.collection.yaml").write_text(
        _LOBBY_YAML.format(rounds=lobby_rounds), encoding="utf-8"
    )
    (workspace / "seeds" / "collections" / "film-lobby.yaml").write_text(
        _LOBBY_COLLECTION, encoding="utf-8"
    )
    return workspace


class TestSeedOnline:
    """Онлайн-шаблоны этапа 4a: публикация, пулы и сборник для лобби (план §0 п. 1–2)."""

    def test_waits_for_templates_and_lobby_collection(self, workspace: Path) -> None:
        stack = FakeStack(online_modes=ONLINE, online_missing_polls=2, playable_items=(FILM_ID,))
        _run(stack, _online(workspace))

        # Шаблоны ждут, пока game не отдаст все онлайн-ключи (опрос, а не одна попытка).
        assert len(stack.recorded("GET", "/v1/modes")) == 3
        # Пулы онлайн-шаблонов ждут тем же поиском проекции, что и соло.
        polled = [
            r.url.params["playable_for_mode"]
            for r in stack.recorded("GET", "/admin/v1/projection/items")
        ]
        assert set(ONLINE) <= set(polled)
        # Сборник лобби проверяется под сам шаблон лобби — после засева сборников.
        paths = [r.url.path for r in stack.requests]
        last_lobby_poll = max(
            i
            for i, r in enumerate(stack.requests)
            if r.url.path == "/admin/v1/projection/items"
            and r.url.params["playable_for_mode"] == "film.lobby.collection"
        )
        assert last_lobby_poll > paths.index("/admin/v1/collections")
        # Шаблоны — до перегенерации плановых наборов (шаг пулов).
        first_regen = next(i for i, p in enumerate(paths) if p.endswith("/regenerate"))
        assert paths.index("/v1/modes") < first_regen

    def test_without_online_templates_skips_step(self, workspace: Path) -> None:
        stack = FakeStack()
        _run(stack, workspace)
        assert not stack.recorded("GET", "/v1/modes")

    def test_missing_template_times_out_with_keys(self, workspace: Path) -> None:
        stack = FakeStack(online_modes=ONLINE[:1], playable_items=(FILM_ID,))
        with pytest.raises(SeedError, match=r"film\.lobby\.collection"):
            _run(stack, _online(workspace))

    def test_unpublished_mode_fails_fast(self, workspace: Path) -> None:
        stack = FakeStack(online_modes=ONLINE, unpublished="music.duel.classic")
        with pytest.raises(SeedError, match="не опубликован"):
            _run(stack, _online(workspace))
        polled = [
            r.url.params["playable_for_mode"]
            for r in stack.recorded("GET", "/admin/v1/projection/items")
        ]
        assert polled.count("music.duel.classic") == 1  # без ожидания до таймаута

    def test_lobby_needs_collection_with_enough_items(self, workspace: Path) -> None:
        # Матч на 3 раунда, а в сборнике лобби одна единица — ошибка до импорта каталога.
        stack = FakeStack(online_modes=ONLINE, playable_items=(FILM_ID,))
        with pytest.raises(SeedError, match=r"film\.lobby\.collection.*≥ 3"):
            _run(stack, _online(workspace, lobby_rounds=3))
        assert not stack.recorded("POST", "/admin/v1/imports")

    def test_lobby_collection_not_playable_times_out(self, workspace: Path) -> None:
        stack = FakeStack(online_modes=ONLINE)  # единица сборника не играбельна под шаблон
        with pytest.raises(SeedError, match=r"под film\.lobby\.collection.*0 из 1"):
            _run(stack, _online(workspace))

    def test_real_seeds_have_collection_for_every_lobby(self) -> None:
        import yaml

        plans = [
            mode_plan(yaml.safe_load(p.read_text("utf-8")))
            for p in sorted((REPO / "seeds/game/modes").glob("*.yaml"))
        ]
        specs = [load_collection_spec(p) for p in _COLLECTIONS]
        picked = {plan.key: spec.key for plan, spec in lobby_collections(plans, specs)}
        # Демонстрация §0 п. 2: «кино по сборнику» на 12 раундов — свой сборник засева.
        assert picked == {"film.lobby.collection": "film-lobby"}


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


# ----------------------------------------------------------------------------- сборники
_COLLECTIONS = sorted((REPO / "seeds" / "collections").glob("*.yaml"))
_META = REPO / "services/collections/src/collections_service/config/meta.yaml"


def _catalog_domains() -> dict[str, str]:
    """Ключ сида → домен по всем файлам импорта."""
    domains = {"film": "film", "track": "music"}
    return {r["key"]: domains[r["kind"]] for name in IMPORT_FILES for r in _rows(name)}


class TestCollectionSeeds:
    """Контракт ``seeds/collections/*.yaml`` с collections и game (план 3b §0 п. 1, §16 п. 5)."""

    def test_three_curated_collections(self) -> None:
        specs = [load_collection_spec(p) for p in _COLLECTIONS]
        modes = {s.default_mode_key for s in specs}
        assert {"film.collection.frame", "film.collection.emoji", "music.collection.intro"} <= modes
        assert len({s.key for s in specs}) == len(specs)
        assert len({s.title for s in specs}) == len(specs)  # по названию засев находит сборник
        # Свой набор эмодзи — хотя бы у одного сборника (AssetsReady со scope в демонстрации).
        assert any(s.emoji_items() for s in specs)

    @pytest.mark.parametrize("path", _COLLECTIONS, ids=lambda p: p.stem)
    def test_matches_meta_modes_and_catalog(self, path: Path) -> None:
        import yaml

        limits = pytest.importorskip("collections_service.settings").CollectionsLimits()
        spec = load_collection_spec(path)
        meta = yaml.safe_load(_META.read_text("utf-8"))
        tags = {t["key"]: t["domains"] for t in meta["tags"]}
        presets = {p["id"] for p in meta["cover_presets"]}
        mode = yaml.safe_load(
            (REPO / "seeds/game/modes" / f"{spec.default_mode_key}.yaml").read_text("utf-8")
        )

        assert path.stem == spec.key
        assert len(spec.title) <= limits.title_max
        assert spec.description is None or len(spec.description) <= limits.description_max
        assert len(spec.tags) <= limits.tags_max
        for tag in spec.tags:
            assert spec.domain in tags[tag], tag
        assert spec.cover in presets
        assert spec.visibility in {"public", "unlisted"}
        assert mode["domain"] == spec.domain
        assert mode["source"]["type"] == "collection"
        min_items = max(limits.items_min, int(mode["source"]["params"]["min_items"]))
        assert min_items <= len(spec.items) <= limits.items_max
        # Сценарий e2e играет 5 раундов: столько единиц нужно и самому малому варианту.
        assert len(spec.items) >= min(
            o for o in mode["source"]["params"]["round_options"] if o != "all"
        )

        catalog = _catalog_domains()
        keys = [i.seed_key for i in spec.items]
        assert len(set(keys)) == len(keys)
        for key in keys:
            assert catalog[key] == spec.domain, key

    @pytest.mark.parametrize("path", _COLLECTIONS, ids=lambda p: p.stem)
    def test_custom_emoji_are_strict(self, path: Path) -> None:
        emoji = pytest.importorskip("gv_text.emoji")
        limits = pytest.importorskip("collections_service.settings").CollectionsLimits()
        for item in load_collection_spec(path).emoji_items():
            text = item.emoji or ""
            assert emoji.is_strict_emoji(text, max_graphemes=limits.emoji_max_graphemes), text
            # Профиль emoji media-pipeline открывает до 6 эмодзи: меньше — набор не строится.
            assert len(emoji.graphemes(text)) >= limits.emoji_min_graphemes, text

    def test_emoji_collection_needs_scoped_sets(self) -> None:
        # Реальные фильмы сидов без эмодзи в каталоге: в режиме emoji они играбельны только
        # через свои эмодзи сборника — так засев проверяет AssetsReady со scope.
        spec = load_collection_spec(REPO / "seeds/collections/film-emoji.yaml")
        stage2 = {r["key"]: r for r in _rows("films.jsonl")}
        custom = [i for i in spec.emoji_items() if i.seed_key in stage2]
        assert len(custom) >= 5
        assert all("clue_texts" not in stage2[i.seed_key] for i in custom)


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
    def test_existing_files_not_regenerated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gen = _load_generator()
        calls: list[Path] = []
        monkeypatch.setattr(gen.subprocess, "run", lambda args, **_: calls.append(Path(args[-1])))
        target = tmp_path / "a.m4a"
        target.write_bytes(b"old")
        gen._ffmpeg_audio("0", 1, target)
        assert calls == []
        assert target.read_bytes() == b"old"
        gen._ffmpeg_audio("0", 1, tmp_path / "b.m4a")
        assert calls == [tmp_path / "b.m4a"]

    def test_ffmpeg_output_is_bitexact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gen = _load_generator()
        seen: list[list[str]] = []
        monkeypatch.setattr(gen.subprocess, "run", lambda args, **_: seen.append(list(args)))
        gen._ffmpeg_audio("0", 1, tmp_path / "c.m4a")
        assert "+bitexact" in seen[0]
        assert seen[0][seen[0].index("-map_metadata") + 1] == "-1"

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
