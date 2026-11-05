"""Предзагрузка: ссылка на уровень 1 следующего раунда (``features.preload_next_round``).

Безопасность: ссылка выдаётся только когда текущий раунд завершён (в раунде не бывает
ссылок на будущие уровни), только уровня 1, без отметки старта (таймер запускает
обычный ``POST .../clue``) и только в режимах, где флаг включён конфигом.
"""

from __future__ import annotations

from typing import Any

import pytest

from .test_stage3a_modes import RANDOM, bearer, bootstrap, give_up, ikey, start, stored

pytestmark = pytest.mark.component


async def _ready(game: Any, items: int = 25) -> None:
    await bootstrap(game)
    for _ in range(items):
        await game.add_item(ready_for=RANDOM)


async def _skip(game: Any, token: str, snap: dict[str, Any]) -> dict[str, Any]:
    body = {"round_index": snap["current_round"], "expected_version": snap["version"]}
    response = await game.client.post(
        f"/v1/sessions/{snap['id']}/skips", json=body, headers=bearer(token, ikey())
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_no_link_at_session_start_or_inside_a_round(game: Any) -> None:
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    assert snap["next_round_first_clue"] is None  # первый раунд - не «следующий»

    skipped = await _skip(game, token, snap)  # уровень открыт, раунд идёт
    assert skipped["next_round_first_clue"] is None
    assert skipped["session"]["next_round_first_clue"] is None
    # ссылок на будущие уровни в ответе нет вообще
    assert not any(str(v).startswith("/media/a/") for v in _urls(skipped))


async def test_finished_round_returns_level_one_link_of_next_round(game: Any) -> None:
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    issued_before = set(game.assets.issued)

    done = (await give_up(game, token, snap)).json()

    clue = done["next_round_first_clue"]
    assert clue is not None
    assert set(clue) == {"level", "kind", "url", "expires_at", "mime", "meta"}
    assert (clue["level"], clue["kind"]) == (1, "audio")
    assert clue["url"].startswith("/media/a/")
    assert done["session"]["next_round_first_clue"] == clue
    after = await stored(game, snap["id"])
    next_round = after.rounds[-1]
    assert next_round.index == 1 and not next_round.finished
    # ссылка уровня 1 именно следующего раунда, выданная этим ходом; таймер не запущен
    new = {(s, lvl) for s, lvl, _ in set(game.assets.issued) - issued_before}
    assert (next_round.asset.asset_set_id, 1) in new
    assert next_round.started_at is None


async def test_link_disappears_once_the_next_round_is_started(game: Any) -> None:
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    done = (await give_up(game, token, snap)).json()
    assert done["next_round_first_clue"] is not None
    session_snap = done["session"]

    started = await game.client.post(
        f"/v1/sessions/{snap['id']}/rounds/1/clue", json={"level": 1}, headers=bearer(token)
    )
    assert started.status_code == 200, started.text
    again = (await game.client.get(f"/v1/sessions/{snap['id']}", headers=bearer(token))).json()
    assert again["next_round_first_clue"] is None
    assert again["version"] >= session_snap["version"]


async def test_snapshot_reread_after_round_end_carries_the_link(game: Any) -> None:
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    await give_up(game, token, snap)

    again = (await game.client.get(f"/v1/sessions/{snap['id']}", headers=bearer(token))).json()

    assert again["next_round_first_clue"]["level"] == 1


async def test_no_link_when_flag_is_off(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from game_service.application.rules import ModeRules

    monkeypatch.setattr(ModeRules, "preload_next_round", property(lambda _: False))
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()

    done = (await give_up(game, token, snap)).json()

    assert done["next_round_first_clue"] is None
    assert done["session"]["next_round_first_clue"] is None


async def test_unavailable_next_asset_gives_null_and_does_not_void_the_round(
    game: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from game_service.application.rules import ModeRules

    from gv_core.errors import GoneError

    async def gone(*args: Any, **kwargs: Any) -> Any:
        raise GoneError("набор выключен", code="asset.blocked")

    monkeypatch.setattr(ModeRules, "clue_view", gone)  # только предзагрузка: give-up его не зовёт
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()

    response = await give_up(game, token, snap)

    assert response.status_code == 200, response.text
    assert response.json()["next_round_first_clue"] is None
    after = await stored(game, snap["id"])
    assert not after.rounds[-1].is_void  # аннулирует только настоящая выдача уровня


async def test_replayed_move_does_not_issue_another_link(game: Any) -> None:
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    key = ikey()
    url = f"/v1/sessions/{snap['id']}/rounds/{snap['current_round']}/give-up"
    body = {"expected_version": snap["version"]}
    first = await game.client.post(url, json=body, headers=bearer(token, key))
    issued = len(game.assets.issued)
    replay = await game.client.post(url, json=body, headers=bearer(token, key))

    assert first.json() == replay.json()
    assert len(game.assets.issued) == issued


async def test_last_round_has_no_next_link(game: Any) -> None:
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    current = snap
    for _ in range(len(snap["rounds"])):
        done = (await give_up(game, token, current)).json()
        current = done["session"]
    assert done["session"]["status"] == "finished"
    assert done["next_round_first_clue"] is None


def _urls(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [x for v in value.values() for x in _urls(v)]
    if isinstance(value, list):
        return [x for v in value for x in _urls(v)]
    return [value]


async def test_foreign_player_cannot_read_the_preload_link(game: Any) -> None:
    from gv_testkit import assert_problem

    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    await give_up(game, token, snap)

    stranger = await game.client.get(
        f"/v1/sessions/{snap['id']}", headers=bearer(game.issuer.issue())
    )

    assert_problem(stranger, 404, "game.session_not_found")


async def test_preload_is_not_issued_inside_the_move_transaction_and_replay_is_stable(
    game: Any,
) -> None:
    """Повтор хода не выдаёт ссылку заново (кэш) и отдаёт ту же, а не сохранённую из БД."""
    await _ready(game)
    token = game.issuer.issue()
    snap = (await start(game, token, RANDOM)).json()
    key = ikey()
    url = f"/v1/sessions/{snap['id']}/rounds/{snap['current_round']}/give-up"
    body = {"expected_version": snap["version"]}

    first = (await game.client.post(url, json=body, headers=bearer(token, key))).json()
    replay = (await game.client.post(url, json=body, headers=bearer(token, key))).json()

    assert first["next_round_first_clue"] is not None
    assert replay["next_round_first_clue"] == first["next_round_first_clue"]
