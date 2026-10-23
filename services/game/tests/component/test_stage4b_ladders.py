"""Проекция ключей ladder-ов (4b, ADR 0037) и проверка ``session.ladder`` в валидаторе.

Проекция строится из ``rating.LadderPublished`` (inbox, порядок по ``version``); game не читает
БД rating и не вызывает его. Ссылка рейтингового режима на неизвестный ladder — ошибка
конфигурации; сид-публикация (bootstrap) ограничивается предупреждением: события rating могут
дойти позже старта game.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from game_service.infrastructure.db.tables import ladders_proj
from sqlalchemy import select

from gv_messaging import Outcome
from gv_testkit import load_example

pytestmark = pytest.mark.component

SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"
RANKED = "film.ranked.classic"
NOT_FOUND = ("session.ladder", "config.ladder_unknown")


def seed_body(key: str) -> dict[str, Any]:
    body: dict[str, Any] = yaml.safe_load((SEEDS_DIR / f"{key}.yaml").read_text("utf-8"))
    return body


async def publish_ladder(
    game: Any, ladder: str = "film", *, version: int = 1, **payload: Any
) -> Outcome:
    base = load_example("rating.LadderPublished")
    domain = payload.pop("domain", "film" if ladder == "film" else "music")
    body = {**base.payload, "ladder": ladder, "domain": domain, "version": version, **payload}
    envelope = base.model_copy(update={"aggregate_id": ladder, "payload": body})
    return await game.deliver(envelope)  # type: ignore[no-any-return]


async def validate(game: Any, body: dict[str, Any]) -> dict[str, Any]:
    response = await game.client.post(
        "/admin/v1/mode-configs/validate",
        json={"body": body},
        headers=game.admin("config.modes.view"),
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def errors(result: dict[str, Any]) -> set[tuple[str, str]]:
    return {(e["path"], e["code"]) for e in result["errors"]}


async def stored(game: Any) -> dict[str, Any]:
    async with game.infra.sessions() as s:
        rows = (await s.execute(select(ladders_proj))).mappings().all()
    return {r["ladder"]: dict(r) for r in rows}


async def test_ladder_published_is_projected(game: Any) -> None:
    assert await publish_ladder(game) is Outcome.PROCESSED

    row = (await stored(game))["film"]
    assert (row["domain"], row["version"]) == ("film", 1)


async def test_stale_and_duplicate_versions_do_not_overwrite(game: Any) -> None:
    await publish_ladder(game, version=3)
    await publish_ladder(game, version=2)  # опоздавшее
    await publish_ladder(game, version=3)  # повтор версии

    assert (await stored(game))["film"]["version"] == 3


async def test_newer_version_replaces_projection(game: Any) -> None:
    await publish_ladder(game, version=1)
    await publish_ladder(game, version=2)

    assert (await stored(game))["film"]["version"] == 2


async def test_invalid_payload_goes_to_dlq(game: Any) -> None:
    outcome = await publish_ladder(game, ladder="Film Bad")

    assert outcome is not Outcome.PROCESSED
    assert await stored(game) == {}


async def test_unknown_ladder_is_config_error(game: Any) -> None:
    result = await validate(game, seed_body(RANKED))

    assert result["ok"] is False
    assert NOT_FOUND in errors(result)


async def test_known_ladder_passes(game: Any) -> None:
    await publish_ladder(game)

    result = await validate(game, seed_body(RANKED))

    assert NOT_FOUND not in errors(result)
    # Пул пуст (контент не загружен) — единственная проблема.
    assert errors(result) <= {("source.params.pool.min_playable", "config.pool_too_small")}


async def test_ladder_of_other_domain_is_rejected(game: Any) -> None:
    await publish_ladder(game, ladder="music", domain="music")
    body = copy.deepcopy(seed_body(RANKED))
    body["session"]["ladder"] = "music"

    result = await validate(game, body)

    assert ("session.ladder", "config.ladder_domain_mismatch") in errors(result)


async def test_solo_and_unranked_modes_do_not_need_ladders(game: Any) -> None:
    result = await validate(game, seed_body("film.duel.frame"))

    assert all(path != "session.ladder" for path, _ in errors(result))


async def test_bootstrap_publishes_ranked_seed_without_projection(game: Any) -> None:
    """Сиды публикуются без ladder-а в проекции: событие rating может прийти позже."""
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"

    detail = await game.client.get(
        f"/admin/v1/mode-configs/{RANKED}", headers=game.admin("config.modes.view")
    )

    assert detail.status_code == 200, detail.text
    assert detail.json()["published_version"] == 1
