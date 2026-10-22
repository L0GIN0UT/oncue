"""Тесты ``gv_testkit.match``: DSL матча ведёт раунды как владелец комнаты, фейки портов
match ведут себя как обещают (контракт realtime, одна ссылка на уровень, сбои плана)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml

from gv_core.errors import DependencyUnavailableError
from gv_game_kernel import (
    AnswerInput,
    AssetRef,
    ConfigError,
    LevelLockedError,
    PlayerStatus,
    PoolTooSmallError,
    RoundFinishedError,
)
from gv_testkit.game import media_item
from gv_testkit.match import (
    FakeLinkPort,
    FakeRealtime,
    FakeRoundPlanPort,
    build_online_engine,
    given_match,
)

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODES = _REPO_ROOT / "libs" / "game_kernel" / "tests" / "modes"
_SOLO = _REPO_ROOT / "seeds" / "game" / "modes" / "film.random.frame.yaml"


def _mode(key: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((_MODES / f"{key}.yaml").read_text(encoding="utf-8"))
    return data


FILM = media_item("Брат", domain="film", aliases=("брат",))
FILM2 = media_item("Сталкер", domain="film", aliases=("сталкер",))


# ----------------------------------------------------------------------------- DSL
def test_duel_race_scenario() -> None:
    """Дуэль: опережение закрывает уровень, соперник играет следующий; итог по окнам."""
    (
        given_match(_mode("film.duel.frame"))
        .with_players("a", "b")
        .with_items(FILM, FILM2)
        .at(1_000)
        .when_guess("b", text="мимо")
        .then_verdict("b", "miss")
        .then_statuses({"a": PlayerStatus.THINKING, "b": "thinking"})
        .at(2_500)
        .when_guess("a", exact=True)
        .then_verdict("a", "exact")
        .then_level_closed()
        .at(3_000)
        .then_level(2)
        .then_level_closed(closed=False)
        .when_guess("b", text="брат")
        .then_round_finished()
        .then_scores({"a": 6, "b": 5})
        # Второй раунд стартует на следующем ходе; никто не угадал — уровни по окнам.
        .at(4_000)
        .when_guess("a", text="нет")
        .then_level(1)
        .at(4_000 + 6 * 15_000)
        .then_round_finished()
        .then_scores({"a": 6, "b": 5})
        .then_places({"a": 1, "b": 2})
    )


def test_locked_level_rejects_late_guess() -> None:
    scenario = (
        given_match(_mode("film.duel.frame"))
        .with_players("a", "b")
        .with_items(FILM)
        .at(10)
        .when_guess("a", exact=True)
    )
    with pytest.raises(LevelLockedError):
        scenario.when_guess("b", exact=True)  # та же миллисекунда, второй по порядку


def test_party_speed_scenario_with_leave() -> None:
    (
        given_match(_mode("film.party.frame"))
        .with_players("a", "b", "c")
        .with_items(FILM, FILM2)
        .at(0)
        .start_round()
        .at(5_000)
        .when_guess("a", exact=True)
        .at(6_000)
        .when_skip("b")
        .when_leave("c")
        .then_level_closed()
        .then_statuses({"a": "guessed", "b": "answered", "c": "missed"})
        .at(16_000)
        .when_guess("b", exact=True)
        .then_round_finished()
        .then_scores({"a": 75, "b": 50, "c": 0})
        .at(20_000)
        .when_guess("b", exact=True)  # второй раунд — без вышедшего c
        .then_statuses({"a": "thinking", "b": "guessed"})
        .then_places({"a": 1, "b": 2, "c": 3})  # второй раунд ещё идёт
        .at(20_000 + 6 * 20_000)
        .then_scores({"a": 75, "b": 150, "c": 0})
        .then_places({"b": 1, "a": 2, "c": 3})
    )


def test_scenario_guards() -> None:
    scenario = given_match(_mode("film.party.frame")).with_items(FILM)
    with pytest.raises(AssertionError, match="не начался"):
        _ = scenario.current
    with pytest.raises(AssertionError, match="нет игроков"):
        scenario.start_round()
    scenario.with_players("a", "b", "c").at(5)
    with pytest.raises(AssertionError, match="назад"):
        scenario.at(1)
    scenario.start_round()
    with pytest.raises(AssertionError, match="идёт"):
        scenario.start_round()
    scenario.when_guess("a", item=FILM).when_guess("b", text="x").when_guess("c", text="y")
    scenario.at(10**7).then_round_finished()
    with pytest.raises(AssertionError, match="кончились"):
        scenario.start_round()
    with pytest.raises(RoundFinishedError):
        scenario.engine.contest_guess(
            scenario.state,
            "a",
            AnswerInput(text="x"),
            scenario.current.draw,
            display="",
            now_ms=10**7,
        )


def test_default_items_follow_session_rounds() -> None:
    scenario = given_match(_mode("film.party.frame"))
    assert len(scenario.rounds) == 10
    assert all(r.item.domain == "film" for r in scenario.rounds)


def test_solo_config_is_rejected() -> None:
    with pytest.raises(ConfigError):
        build_online_engine(yaml.safe_load(_SOLO.read_text(encoding="utf-8")))


# ----------------------------------------------------------------------------- порты
async def test_fake_round_plan_port() -> None:
    port = FakeRoundPlanPort(plan={"rounds": []})
    assert await port.create_round_plan({"seed": "s"}) == {"rounds": []}
    port.pool_too_small(available=3, required=10)
    with pytest.raises(PoolTooSmallError) as info:
        await port.create_round_plan({"seed": "t"})
    assert info.value.details == {"available": 3, "required": 10}
    port.unavailable()
    with pytest.raises(DependencyUnavailableError):
        await port.create_round_plan({"seed": "u"})
    port.heal()
    by_request = FakeRoundPlanPort(factory=lambda req: {"echo": req["seed"]})
    assert await by_request.create_round_plan({"seed": "v"}) == {"echo": "v"}
    assert [r["seed"] for r in port.requests] == ["s", "t", "u"]
    with pytest.raises(AssertionError, match="plan или factory"):
        await FakeRoundPlanPort().create_round_plan({})


def _example(kind: str) -> dict[str, Any]:
    path = _REPO_ROOT / "contracts" / "realtime" / "examples" / f"room.message.{kind}.v1.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


@pytest.fixture
def contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GV_CONTRACTS_DIR", str(_REPO_ROOT / "contracts"))


@pytest.mark.usefixtures("contracts")
async def test_fake_realtime_validates_and_tracks_versions() -> None:
    rt = FakeRealtime()
    first, second = _example("level_opened"), _example("level_closed")
    second["version"] = first["version"] + 1
    await rt.publish("room:ABCD2345", first)
    await rt.publish("room:ABCD2345", second)
    await rt.publish("room:OTHER234", _example("phase"))
    assert rt.kinds("room:ABCD2345") == ["level_opened", "level_closed"]
    assert len(rt.room("OTHER234")) == 1
    rt.assert_versions_contiguous("room:ABCD2345")
    rt.assert_versions_contiguous("room:EMPTY234")
    leaked = _example("level_opened")
    leaked["delta"]["media_item_id"] = "0192a3b4-c5d6-7e8f-9a0b-000000000101"
    with pytest.raises(AssertionError):
        await rt.publish("room:ABCD2345", leaked)
    gap = _example("level_closed")
    gap["version"] = first["version"] + 5
    await rt.publish("room:ABCD2345", gap)
    with pytest.raises(AssertionError, match="версии"):
        rt.assert_versions_contiguous("room:ABCD2345")
    rt.unavailable()
    with pytest.raises(DependencyUnavailableError):
        await rt.publish("room:ABCD2345", first)
    rt.heal()
    await rt.publish("room:ABCD2345", first)


async def test_fake_link_port_single_link_per_level() -> None:
    port = FakeLinkPort()
    asset = AssetRef(asset_set_id=UUID(int=1), levels_count=6)
    await port.link(asset, 1, "room:ABCD2345:0")
    await port.link(asset, 2, "room:ABCD2345:0")
    await port.link(asset, 1, "room:ABCD2345:1")
    port.assert_single_link_per_level()
    assert port.subjects() == ["room:ABCD2345:0", "room:ABCD2345:1"]
    assert port.levels_by_subject() == {"room:ABCD2345:0": [1, 2], "room:ABCD2345:1": [1]}
    await port.link(asset, 2, "room:ABCD2345:0")
    assert len(port.duplicates()) == 1
    with pytest.raises(AssertionError, match="повторные"):
        port.assert_single_link_per_level()
