"""Формат «лайт» (этап 4b, ADR 0040): ``round-plans`` отдаёт варианты ``pick`` с дистракторами.

Шаблон ``*.duel.lite`` — шаг 0b; здесь режим собирается из сида дуэли и публикуется тем же
bootstrap, что и сиды. Подбор вариантов — плагин ядра ``similar`` из ``answer.params.distractors``.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from game_service.infrastructure.container import container_for

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

LITE = "music.duel.litetest"
SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"
SIMILAR = {"same_genre": True, "year_window": 10, "popularity_ratio": None, "min_pool": 8}


def lite_body(**distractors: Any) -> dict[str, Any]:
    """Дуэль «лайт»: один уровень, выбор из ``count + 1`` вариантов, одна попытка."""
    body: dict[str, Any] = copy.deepcopy(
        yaml.safe_load((SEEDS_DIR / "music.duel.classic.yaml").read_text("utf-8"))
    )
    body["key"] = LITE
    body["source"]["params"]["pool"]["min_playable"] = 10
    body["clue"]["params"]["levels"] = [{"seconds": 4}]
    body["scoring"]["params"]["points"] = [12]
    body["answer"]["format"] = "pick"
    body["answer"]["partial"] = []
    body["answer"]["params"] = {
        "distractors": {"type": "similar", "count": 3, "params": SIMILAR} | distractors
    }
    body["session"]["rounds"] = 3
    body["session"]["level_windows_s"] = [20]
    body["session"]["contest"] = {
        "type": "parallel",
        "params": {"attempts_per_level": 1, "end_when_all_answered": True},
    }
    return body


@pytest.fixture
def match_headers(service_token: Callable[..., str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {service_token('match', 'game', ['rounds.plan'])}"}


async def publish(game: Any, tmp_path: Path, body: dict[str, Any]) -> None:
    (tmp_path / f"{LITE}.yaml").write_text(yaml.safe_dump(body, allow_unicode=True), "utf-8")
    assert await container_for(game.infra).bootstrap.execute(tmp_path) == [LITE]


async def tracks(game: Any, n: int, *, year: int = 2005) -> list[UUID]:
    found: list[UUID] = []
    for _ in range(n):
        item = await game.add_item(domain="music", year=year)
        await game.ready_assets(item, LITE, body=lite_body())
        found.append(item)
    return found


async def plan(game: Any, headers: dict[str, str], **body: Any) -> Any:
    payload = {"mode_key": LITE, "seed": "match-0000000001", "rounds": 3, **body}
    return await game.client.post("/internal/v1/round-plans", json=payload, headers=headers)


async def test_round_plan_has_options_with_target_inside(
    game: Any, tmp_path: Path, match_headers: dict[str, str]
) -> None:
    await publish(game, tmp_path, lite_body())
    items = await tracks(game, 20)

    response = await plan(game, match_headers)

    assert response.status_code == 200, response.text
    for round_ in response.json()["rounds"]:
        options = round_["options"]
        assert len(options) == 4
        ids = [o["media_item_id"] for o in options]
        assert round_["answer"]["media_item_id"] in ids
        assert len(set(ids)) == 4 and {UUID(i) for i in ids} <= set(items)
        assert len({o["option_id"] for o in options}) == 4
        # option_id непрозрачны: не совпадают ни с id единицы, ни с названием.
        assert all(o["option_id"] not in (o["media_item_id"], o["title"]) for o in options)


async def test_round_plan_options_deterministic_by_seed(
    game: Any, tmp_path: Path, match_headers: dict[str, str]
) -> None:
    await publish(game, tmp_path, lite_body())
    await tracks(game, 20)

    a = await plan(game, match_headers)
    b = await plan(game, match_headers)
    c = await plan(game, match_headers, seed="match-0000000002")

    assert a.json() == b.json()
    assert a.json()["rounds"] != c.json()["rounds"]


async def test_options_follow_mode_config(
    game: Any, tmp_path: Path, match_headers: dict[str, str]
) -> None:
    """Окно по году из конфига: единицы далеко по году в варианты не попадают."""
    await publish(game, tmp_path, lite_body())
    near = await tracks(game, 12, year=2005)
    far = await tracks(game, 6, year=1960)

    response = await plan(game, match_headers, rounds=3)

    assert response.status_code == 200, response.text
    for round_ in response.json()["rounds"]:
        ids = {UUID(o["media_item_id"]) for o in round_["options"]}
        target = UUID(round_["answer"]["media_item_id"])
        assert ids <= set(near) if target in near else ids <= set(far)


async def test_too_few_candidates_for_options(
    game: Any, tmp_path: Path, match_headers: dict[str, str]
) -> None:
    await publish(game, tmp_path, lite_body())
    await tracks(game, 10)
    await tracks(game, 1, year=1900)  # у неё нет соседей по году

    # Раундов хватает, но у «одиночки» нет трёх близких вариантов: раунд недоступен.
    response = await plan(game, match_headers, rounds=11)

    assert_problem(response, 422, "game.pool_too_small")


async def test_admin_rejects_distractors_with_classic_format(game: Any) -> None:
    body = lite_body()
    body["answer"]["format"] = "classic"
    response = await game.client.post(
        "/admin/v1/mode-configs/validate",
        json={"body": body},
        headers=game.admin("config.modes.view"),
    )
    codes = {(e["path"], e["code"]) for e in response.json()["errors"]}
    assert ("answer.params.distractors", "config.distractors_format") in codes
