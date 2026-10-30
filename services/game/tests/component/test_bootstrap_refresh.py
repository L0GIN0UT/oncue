"""Bootstrap сидов: правка сида выпускает новую версию режима после готовности ассетов."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from game_service.application.commands import modes as modes_module

from gv_core.ids import uuid7

pytestmark = pytest.mark.component

KEY = "film.daily.frame"
VIEW, EDIT, PUBLISH = "config.modes.view", "config.modes.edit", "config.modes.publish"


def _changed_seed(blur_first: float) -> dict[str, Any]:
    """Сид режима с другим размытием первого уровня (новый ``params_hash``)."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes" / f"{KEY}.yaml"
    body: dict[str, Any] = yaml.safe_load(path.read_text("utf-8"))
    body = copy.deepcopy(body)
    body["clue"]["params"]["levels"][0]["blur_px"] = blur_first
    return body


def _serve_only(monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]) -> None:
    monkeypatch.setattr(modes_module, "_read_seeds", lambda _directory: [(f"{KEY}.yaml", body)])


async def _versions(game: Any) -> list[int]:
    detail = await game.client.get(f"/admin/v1/mode-configs/{KEY}", headers=game.admin(VIEW))
    assert detail.status_code == 200, detail.text
    return [v["version"] for v in detail.json()["versions"]]


async def _bootstrap(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"


async def test_changed_seed_waits_for_assets_then_publishes_new_version(
    game: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пока под новый params_hash нет ассетов, режим остаётся на v1; потом выходит v2.
    Наборы дня и сессии старой версии доигрываются по старым ассетам."""
    await _bootstrap(game)
    assert await _versions(game) == [1]
    items = [await game.add_item(domain="film", ready_for=KEY, year=2000 + i) for i in range(12)]
    new_body = _changed_seed(24)
    _serve_only(monkeypatch, new_body)

    await _bootstrap(game)
    assert await _versions(game) == [1]  # ассетов под новый хэш ещё нет

    for item in items[:5]:
        await game.ready_assets(item, KEY, body=new_body, version=2)
    await _bootstrap(game)
    assert await _versions(game) == [1]  # есть ассеты, но пул меньше min_playable (10)

    for item in items[5:]:
        await game.ready_assets(item, KEY, body=new_body, version=2)
    await _bootstrap(game)
    assert await _versions(game) == [1, 2]
    await _bootstrap(game)
    assert await _versions(game) == [1, 2]  # повтор ничего не выпускает

    detail = (
        await game.client.get(f"/admin/v1/mode-configs/{KEY}", headers=game.admin(VIEW))
    ).json()
    latest = await game.client.get(
        f"/admin/v1/mode-configs/{KEY}/versions/2", headers=game.admin(VIEW)
    )
    assert latest.json()["body"]["clue"]["params"]["levels"][0]["blur_px"] == 24
    old = await game.client.get(
        f"/admin/v1/mode-configs/{KEY}/versions/1", headers=game.admin(VIEW)
    )
    assert old.json()["body"]["clue"]["params"]["levels"][0]["blur_px"] != 24
    assert detail["draft"] is None


async def test_admin_version_is_not_overwritten_by_seed(
    game: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Версию, опубликованную админом, правка сида не затирает."""
    await _bootstrap(game)
    items = [await game.add_item(domain="film", ready_for=KEY, year=2000 + i) for i in range(12)]
    manual = _changed_seed(30)
    for item in items:
        await game.ready_assets(item, KEY, body=manual, version=2)
    draft = await game.client.put(
        f"/admin/v1/mode-configs/{KEY}/draft",
        json={"body": manual, "base_revision": None, "reason": "ручная правка"},
        headers={**game.admin(EDIT), "Idempotency-Key": f"k-{uuid7().hex}"},
    )
    assert draft.status_code == 200, draft.text
    pub = await game.client.post(
        f"/admin/v1/mode-configs/{KEY}/publish",
        json={"draft_revision": 1, "reason": "публикация"},
        headers={**game.admin(PUBLISH), "Idempotency-Key": f"k-{uuid7().hex}"},
    )
    assert pub.status_code == 200, pub.text
    assert await _versions(game) == [1, 2]

    new_body = _changed_seed(22)
    for item in items:
        await game.ready_assets(item, KEY, body=new_body, version=3)
    _serve_only(monkeypatch, new_body)
    await _bootstrap(game)
    assert await _versions(game) == [1, 2]


async def test_draft_blocks_seed_refresh(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Незавершённый черновик админа не стирается обновлением сида."""
    await _bootstrap(game)
    items = [await game.add_item(domain="film", ready_for=KEY, year=2000 + i) for i in range(12)]
    draft = await game.client.put(
        f"/admin/v1/mode-configs/{KEY}/draft",
        json={"body": _changed_seed(30), "base_revision": None, "reason": "черновик"},
        headers={**game.admin(EDIT), "Idempotency-Key": f"k-{uuid7().hex}"},
    )
    assert draft.status_code == 200, draft.text
    new_body = _changed_seed(22)
    for item in items:
        await game.ready_assets(item, KEY, body=new_body, version=2)
    _serve_only(monkeypatch, new_body)
    await _bootstrap(game)
    assert await _versions(game) == [1]
