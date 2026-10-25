"""Переопределения ``clue`` и ``answer.format`` хозяина лобби в ``round-plans`` (ADR 0070).

Что разрешено — ``session.lobby.overridable`` шаблона; сочетание проверяет валидатор ядра
(как конфиг при публикации); пул должен быть играбелен именно под переопределённую загадку.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from game_service.infrastructure.container import container_for

from gv_testkit import assert_problem

pytestmark = pytest.mark.component

KEY = "music.lobby.ovtest"
SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"
REVERSE = {"type": "audio_reverse", "params": {"start_s": 0, "levels": [{"seconds": 15}]}}


def lobby_body() -> dict[str, Any]:
    body: dict[str, Any] = copy.deepcopy(
        yaml.safe_load((SEEDS_DIR / "music.lobby.classic.yaml").read_text("utf-8"))
    )
    body["key"] = KEY
    over = body["session"]["lobby"]["overridable"]
    over["clue"] = [body["clue"], REVERSE]
    over["answer_format"] = ["classic", "pick"]
    return body


@pytest.fixture
def headers(service_token: Callable[..., str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {service_token('match', 'game', ['rounds.plan'])}"}


async def publish(game: Any, tmp_path: Path) -> None:
    (tmp_path / f"{KEY}.yaml").write_text(yaml.safe_dump(lobby_body(), allow_unicode=True), "utf-8")
    assert await container_for(game.infra).bootstrap.execute(tmp_path) == [KEY]


async def tracks(game: Any, n: int, *, clue: dict[str, Any] | None = None) -> dict[Any, Any]:
    """Единицы с готовыми ассетами базовой загадки (или ``clue``) -> ``{id: asset_set_id}``."""
    body = lobby_body()
    if clue is not None:
        body["clue"] = clue
    found = {}
    for _ in range(n):
        item = await game.add_item(domain="music")
        found[item] = await game.ready_assets(item, KEY, body=body)
    return found


async def plan(game: Any, headers: dict[str, str], **body: Any) -> Any:
    payload = {"mode_key": KEY, "seed": "match-0000000001", "rounds": 5, **body}
    return await game.client.post("/internal/v1/round-plans", json=payload, headers=headers)


async def test_clue_override_plans_assets_of_overridden_clue(
    game: Any, tmp_path: Path, headers: dict[str, str]
) -> None:
    await publish(game, tmp_path)
    base = await tracks(game, 6)
    reverse = {
        item: await game.ready_assets(item, KEY, body={**lobby_body(), "clue": REVERSE}, version=2)
        for item in base
    }

    plain = await plan(game, headers)
    over = await plan(game, headers, overrides={"clue_type": "audio_reverse"})

    assert plain.status_code == 200 and over.status_code == 200, over.text
    for round_ in plain.json()["rounds"]:
        item = next(i for i in base if str(i) == round_["answer"]["media_item_id"])
        assert round_["levels"][0]["asset_set_id"] == str(base[item])
    for round_ in over.json()["rounds"]:
        item = next(i for i in base if str(i) == round_["answer"]["media_item_id"])
        assert round_["levels"][0]["asset_set_id"] == str(reverse[item])


async def test_pool_must_be_playable_for_overridden_clue(
    game: Any, tmp_path: Path, headers: dict[str, str]
) -> None:
    await publish(game, tmp_path)
    await tracks(game, 8)  # ассетов audio_reverse нет

    response = await plan(game, headers, overrides={"clue_type": "audio_reverse"})

    problem = assert_problem(response, 422, "game.pool_too_small")
    assert problem["details"]["available"] == 0


async def test_override_not_in_overridable_is_rejected(
    game: Any, tmp_path: Path, headers: dict[str, str]
) -> None:
    await publish(game, tmp_path)
    await tracks(game, 6)

    response = await plan(game, headers, overrides={"clue_type": "soundtrack"})

    problem = assert_problem(response, 422, "game.override_not_allowed")
    assert problem["details"] == {"field": "clue"}


async def test_incompatible_format_is_rejected_by_kernel_validator(
    game: Any, tmp_path: Path, headers: dict[str, str]
) -> None:
    """``pick`` разрешён списком, но шаблон играет 3 попытки уровня: валидатор ядра отказывает."""
    await publish(game, tmp_path)
    await tracks(game, 6)

    response = await plan(game, headers, overrides={"answer_format": "pick"})

    problem = assert_problem(response, 422, "game.override_invalid")
    assert problem["details"]["errors"]


async def test_empty_overrides_equal_no_overrides(
    game: Any, tmp_path: Path, headers: dict[str, str]
) -> None:
    await publish(game, tmp_path)
    await tracks(game, 6)

    a = await plan(game, headers)
    b = await plan(game, headers, overrides={})

    assert a.json() == b.json()
