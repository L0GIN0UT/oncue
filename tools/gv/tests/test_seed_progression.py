"""Шаг ``gv dev seed`` для progression: предметы, картинки, паки, конфиг с товаром аватарки.

Фейковый progression на ``httpx.MockTransport`` хранит предметы, паки и версии конфига; тесты
проверяют идемпотентность (повторный запуск без мутаций), догрузку картинки, порядок публикации
конфига (после создания предмета) и ошибки сервиса (409/422).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from gv_cli.seed import SeedConfig, Seeder, SeedError

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
SEEDS = REPO / "seeds" / "progression"
_UUID = "[0-9a-f-]{36}"


def _sha(config: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def _problem(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "status": status})


@dataclass
class FakeProgression:
    """Admin-API progression: предметы, паки, версии конфига."""

    published: dict[str, Any] | None = None
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    packs: dict[str, dict[str, Any]] = field(default_factory=dict)
    versions: list[dict[str, Any]] = field(default_factory=list)
    draft: dict[str, Any] | None = None
    fail: dict[tuple[str, str], int] = field(default_factory=dict)  # (метод, regex пути) -> статус
    requests: list[httpx.Request] = field(default_factory=list)
    draft_changed_once: bool = False  # первая публикация ответит 409 draft_changed

    def __post_init__(self) -> None:
        if self.published is None:
            self.published = yaml.safe_load((SEEDS / "config.yaml").read_text("utf-8"))
        self.versions = [{"version": 1, "status": "published", "config": self.published}]

    def client_factory(self, base_url: str) -> httpx.Client:
        return httpx.Client(base_url=base_url, transport=httpx.MockTransport(self.handle))

    def sent(self, method: str, pattern: str) -> list[httpx.Request]:
        return [
            r for r in self.requests if r.method == method and re.fullmatch(pattern, r.url.path)
        ]

    def mutations(self) -> list[httpx.Request]:
        return [
            r
            for r in self.requests
            if r.method in {"POST", "PUT"} and r.url.path.startswith("/admin/v1/")
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path, method = request.url.path, request.method
        if path == "/internal/v1/service-tokens":
            return httpx.Response(200, json={"access_token": "svc-tok"})
        if path == "/internal/v1/admin-sessions":
            return httpx.Response(200, json={"access_token": "admin-tok"})
        assert request.headers["Authorization"] == "Bearer admin-tok", path
        if method in {"POST", "PUT"}:
            assert len(request.headers["Idempotency-Key"]) >= 8
        for (fail_method, pattern), status in self.fail.items():
            if method == fail_method and re.fullmatch(pattern, path):
                return _problem(status, f"progression.test_{status}")
        routes = [
            ("GET", r"/admin/v1/configs", self._versions),
            ("GET", r"/admin/v1/configs/\d+", self._version),
            ("PUT", r"/admin/v1/configs/draft", self._save_draft),
            ("POST", r"/admin/v1/configs/draft/publish", self._publish),
            ("GET", r"/admin/v1/items", self._list_items),
            ("POST", r"/admin/v1/items", self._create_item),
            ("PUT", rf"/admin/v1/items/{_UUID}/media", self._upload),
            ("PUT", rf"/admin/v1/items/{_UUID}", self._update_item),
            ("GET", r"/admin/v1/packs", self._list_packs),
            ("POST", r"/admin/v1/packs", self._create_pack),
        ]
        for route_method, pattern, responder in routes:
            if method == route_method and re.fullmatch(pattern, path):
                return responder(request)
        return _problem(404, "not_found")

    def _rows(self) -> list[dict[str, Any]]:
        return [*self.versions, *([self.draft] if self.draft else [])]

    @staticmethod
    def _card(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": row["version"],
            "status": row["status"],
            "body_sha256": _sha(row["config"]),
        }

    def _versions(self, _: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [self._card(r) for r in reversed(self._rows())]})

    def _version(self, request: httpx.Request) -> httpx.Response:
        number = int(request.url.path.rsplit("/", 1)[1])
        row = next(v for v in self._rows() if v["version"] == number)
        return httpx.Response(200, json={"card": self._card(row), "config": row["config"]})

    def _save_draft(self, request: httpx.Request) -> httpx.Response:
        config = json.loads(request.content)["config"]
        self.draft = {"version": len(self.versions) + 1, "status": "draft", "config": config}
        return httpx.Response(200, json={"card": self._card(self.draft), "config": config})

    def _publish(self, request: httpx.Request) -> httpx.Response:
        assert self.draft is not None
        body = json.loads(request.content)
        assert body["version"] == self.draft["version"]
        if self.draft_changed_once or body["body_sha256"] != _sha(self.draft["config"]):
            self.draft_changed_once = False
            return _problem(409, "progression.draft_changed")
        key = self.draft["config"]["shop"]["custom_avatar_item_key"]
        if key is not None and key not in self.items:  # как _check_custom_avatar сервиса
            return _problem(422, "progression.config_invalid")
        for v in self.versions:
            v["status"] = "archived"
        self.versions.append({**self.draft, "status": "published"})
        self.draft = None
        return httpx.Response(200, json={})

    def _list_items(self, _: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": list(self.items.values()), "next_cursor": None})

    def _create_item(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["key"] in self.items:
            return _problem(409, "progression.item_key_taken")
        assert body["active"] is False
        shop = self.versions[0]["config"].get("shop") or {}
        if body["slot"] not in {s["key"] for s in shop.get("slots", [])}:
            return _problem(422, "progression.item_slot_unknown")
        if body["rarity"] not in {r["key"] for r in shop.get("rarities", [])}:
            return _problem(422, "progression.item_rarity_unknown")
        if not body.get("level_required") and not body.get("price") and not body.get("event_key"):
            return _problem(422, "progression.item_unlock_required")
        item = {
            **{k: v for k, v in body.items() if k != "reason"},
            "id": f"00000000-0000-0000-0000-{len(self.items) + 1:012d}",
            "asset_url": None,
        }
        self.items[body["key"]] = item
        return httpx.Response(201, json=item)

    def _by_id(self, request: httpx.Request) -> dict[str, Any]:
        item_id = request.url.path.split("/")[4]
        return next(i for i in self.items.values() if i["id"] == item_id)

    def _upload(self, request: httpx.Request) -> httpx.Response:
        item = self._by_id(request)
        assert b'name="file"' in request.content and b"RIFF" in request.content  # WebP
        assert b'name="reason"' in request.content
        media = self.versions[0]["config"]["shop"]["media"]
        limit = media[
            "animated_max_bytes" if item["media_kind"] == "animated" else "static_max_bytes"
        ]
        if len(request.content) > limit + 1000:  # + служебные части multipart
            return _problem(413, "progression.item_media_too_large")
        item["asset_url"] = f"https://cdn.test/c/{item['key']}.webp"
        return httpx.Response(200, json=item)

    def _update_item(self, request: httpx.Request) -> httpx.Response:
        item = self._by_id(request)
        body = json.loads(request.content)
        if body["active"] and not item["asset_url"]:
            return _problem(422, "progression.item_media_missing")
        item.update({k: v for k, v in body.items() if k != "reason"})
        return httpx.Response(200, json=item)

    def _list_packs(self, _: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": list(self.packs.values())})

    def _create_pack(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert all(i in {x["id"] for x in self.items.values()} for i in body["item_ids"])
        self.packs[body["key"]] = body
        return httpx.Response(201, json=body)


def _seeder(stack: FakeProgression, progression_dir: Path = SEEDS) -> Seeder:
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


def _run(stack: FakeProgression, progression_dir: Path = SEEDS) -> None:
    with _seeder(stack, progression_dir) as seeder:
        seeder.authenticate()
        seeder.seed_progression()


ITEM_KEYS = {
    i["key"] for i in yaml.safe_load((SEEDS / "items" / "items.yaml").read_text("utf-8"))["items"]
}


def test_creates_items_media_pack_and_publishes_config_with_avatar_key() -> None:
    stack = FakeProgression()

    _run(stack)

    assert set(stack.items) == ITEM_KEYS
    assert all(i["asset_url"] and i["active"] for i in stack.items.values())
    assert set(stack.packs) == {"starter_pack"}
    assert stack.packs["starter_pack"]["active"] is False
    assert stack.versions[-1]["version"] == 2
    assert stack.versions[-1]["status"] == "published"
    assert stack.versions[-1]["config"]["shop"]["custom_avatar_item_key"] == "custom_avatar"
    # Конфиг v1 (bootstrap) не тронут и остаётся с null: порядок «предметы -> версия с ключом».
    assert stack.versions[0]["config"]["shop"]["custom_avatar_item_key"] is None
    paths = [(r.method, r.url.path) for r in stack.mutations()]
    assert paths.index(("PUT", "/admin/v1/configs/draft")) > max(
        i for i, (m, p) in enumerate(paths) if p == "/admin/v1/items" and m == "POST"
    )


def test_rerun_makes_no_mutations() -> None:
    stack = FakeProgression()
    _run(stack)
    done = len(stack.mutations())

    _run(stack)

    assert len(stack.mutations()) == done
    assert len(stack.versions) == 2


def test_uploads_media_for_existing_item_without_picture_and_activates_it() -> None:
    stack = FakeProgression()
    _run(stack)
    stack.items["frm_neon"].update(asset_url=None, active=False)
    before = len(stack.mutations())

    _run(stack)

    new = stack.mutations()[before:]
    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in new] == [
        ("PUT", "media"),
        ("PUT", stack.items["frm_neon"]["id"]),
    ]
    assert stack.items["frm_neon"]["active"] and stack.items["frm_neon"]["asset_url"]


def test_item_key_conflict_on_create_reuses_existing() -> None:
    stack = FakeProgression()
    _run(stack)
    # Сервис знает предмет, но список ещё не отдаёт его (гонка): 409 -> берём созданный.
    original = stack.handle
    state = {"hide": True}

    def handle(request: httpx.Request) -> httpx.Response:
        if state["hide"] and request.method == "GET" and request.url.path == "/admin/v1/items":
            state["hide"] = False
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        return original(request)

    stack.handle = handle  # type: ignore[method-assign]
    done = len(stack.mutations())

    _run(stack)

    assert set(stack.items) == ITEM_KEYS
    # Новые мутации - только отклонённые 409 создания; картинка заново не грузится.
    new = stack.mutations()[done:]
    assert {(r.method, r.url.path) for r in new} == {("POST", "/admin/v1/items")}
    assert len(stack.sent("PUT", rf"/admin/v1/items/{_UUID}/media")) == len(ITEM_KEYS)


def test_admin_set_avatar_key_is_not_overwritten() -> None:
    stack = FakeProgression()
    stack.versions[0]["config"]["shop"]["custom_avatar_item_key"] = "ava_fox"

    _run(stack)

    assert stack.draft is None
    assert len(stack.versions) == 1
    assert stack.versions[0]["config"]["shop"]["custom_avatar_item_key"] == "ava_fox"
    assert not stack.sent("PUT", "/admin/v1/configs/draft")


def test_foreign_admin_draft_is_not_overwritten() -> None:
    stack = FakeProgression()
    foreign = json.loads(json.dumps(stack.versions[0]["config"]))
    foreign["timezone"] = "Asia/Tokyo"
    stack.draft = {"version": 2, "status": "draft", "config": foreign}

    _run(stack)

    assert stack.draft is not None and stack.draft["config"]["timezone"] == "Asia/Tokyo"
    assert not stack.sent("PUT", "/admin/v1/configs/draft")
    assert not stack.sent("POST", "/admin/v1/configs/draft/publish")
    assert set(stack.items) == ITEM_KEYS  # предметы при этом засеяны


def test_partial_publish_failure_is_resumed_on_rerun() -> None:
    stack = FakeProgression(fail={("POST", "/admin/v1/configs/draft/publish"): 500})

    with pytest.raises(SeedError, match="публикация конфига"):
        _run(stack)
    assert stack.draft is not None  # черновик сохранён, публикация упала
    stack.fail.clear()

    _run(stack)

    assert stack.draft is None
    assert stack.versions[-1]["config"]["shop"]["custom_avatar_item_key"] == "custom_avatar"
    keys = [r.headers["Idempotency-Key"] for r in stack.sent("PUT", "/admin/v1/configs/draft")]
    assert len(keys) == 2 and len(set(keys)) == 1  # тот же ключ: сервис вернёт сохранённый ответ
    assert re.fullmatch(r"seed-config-draft-[0-9a-f]{12}", keys[0])


def test_draft_changed_on_publish_is_retried_once() -> None:
    stack = FakeProgression(draft_changed_once=True)

    _run(stack)

    assert len(stack.sent("POST", "/admin/v1/configs/draft/publish")) == 2
    assert stack.versions[-1]["config"]["shop"]["custom_avatar_item_key"] == "custom_avatar"


def test_persistent_draft_changed_fails_after_one_retry() -> None:
    stack = FakeProgression(fail={("POST", "/admin/v1/configs/draft/publish"): 409})

    with pytest.raises(SeedError, match="409"):
        _run(stack)
    assert len(stack.sent("POST", "/admin/v1/configs/draft/publish")) == 1  # не draft_changed


def test_published_config_without_shop_gives_clear_error() -> None:
    stack = FakeProgression()
    del stack.versions[0]["config"]["shop"]

    with pytest.raises(SeedError, match="перезапустите progression"):
        _run(stack)
    assert stack.mutations() == []


def test_admin_deactivated_item_with_picture_is_not_reactivated() -> None:
    stack = FakeProgression()
    _run(stack)
    stack.items["ava_fox"]["active"] = False
    before = len(stack.mutations())

    _run(stack)

    assert len(stack.mutations()) == before
    assert stack.items["ava_fox"]["active"] is False


def test_changed_yaml_fields_do_not_update_existing_item() -> None:
    stack = FakeProgression()
    _run(stack)
    stack.items["ava_fox"]["title"] = "Переименовано админом"
    before = len(stack.mutations())

    _run(stack)

    assert len(stack.mutations()) == before
    assert stack.items["ava_fox"]["title"] == "Переименовано админом"


def test_item_idempotency_keys_include_field_hash(tmp_path: Path) -> None:
    stack = FakeProgression()
    _run(stack)
    keys = {r.headers["Idempotency-Key"] for r in stack.sent("POST", "/admin/v1/items")} | {
        r.headers["Idempotency-Key"] for r in stack.sent("PUT", rf"/admin/v1/items/{_UUID}")
    }

    assert all(
        re.fullmatch(r"seed-item-(create|activate)-[a-z0-9_]+-[0-9a-f]{12}", k) for k in keys
    )


def _copy_seeds(tmp_path: Path) -> Path:
    root = tmp_path / "progression"
    shutil.copytree(SEEDS, root, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def test_unknown_slot_in_seed_is_reported_as_422(tmp_path: Path) -> None:
    root = _copy_seeds(tmp_path)
    path = root / "items" / "items.yaml"
    path.write_text(path.read_text("utf-8").replace("slot: frame", "slot: hat", 1), "utf-8")

    with pytest.raises(SeedError, match=r"создание предмета.*422.*item_slot_unknown"):
        _run(FakeProgression(), root)


def test_item_without_unlock_condition_is_reported_as_422(tmp_path: Path) -> None:
    root = _copy_seeds(tmp_path)
    path = root / "items" / "items.yaml"
    text = path.read_text("utf-8").replace(
        "level_required: 2, price: null", "level_required: null, price: null"
    )
    path.write_text(text, "utf-8")

    with pytest.raises(SeedError, match=r"item_unlock_required"):
        _run(FakeProgression(), root)


def test_oversized_picture_is_reported_as_413(tmp_path: Path) -> None:
    root = _copy_seeds(tmp_path)
    image = root / "items" / "images" / "ava_fox.webp"
    image.write_bytes(b"RIFF" + b"\0" * 60_000)

    with pytest.raises(SeedError, match=r"картинка предмета ava_fox.*413"):
        _run(FakeProgression(), root)


def test_waits_for_published_config() -> None:
    stack = FakeProgression()
    stack.versions[0]["status"] = "draft"

    with pytest.raises(SeedError, match="опубликованный конфиг"):
        _run(stack)
    assert stack.mutations() == []


def test_unprocessable_item_is_reported_with_code() -> None:
    stack = FakeProgression(fail={("POST", "/admin/v1/items"): 422})

    with pytest.raises(SeedError, match=r"создание предмета \w+.*422.*progression.test_422"):
        _run(stack)


def test_config_publish_conflict_is_reported() -> None:
    stack = FakeProgression(fail={("POST", "/admin/v1/configs/draft/publish"): 409})

    with pytest.raises(SeedError, match=r"публикация конфига progression.*409"):
        _run(stack)


def test_missing_picture_names_generator(tmp_path: Path) -> None:
    root = tmp_path / "progression"
    shutil.copytree(SEEDS, root, ignore=shutil.ignore_patterns("images", "__pycache__"))

    with pytest.raises(SeedError, match=r"generate_items.py"):
        _run(FakeProgression(), root)


def test_no_progression_seeds_skips_step(tmp_path: Path) -> None:
    stack = FakeProgression()

    _run(stack, tmp_path)

    assert stack.mutations() == []
    assert all(r.url.path.startswith("/internal/") for r in stack.requests)
