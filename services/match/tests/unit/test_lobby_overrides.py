"""Переопределения ``clue`` и ``answer.format`` хозяина лобби (ADR 0070): допустимость — из
``session.lobby.overridable`` шаблона, переопределения уходят в план game и хранятся в снимке."""

from __future__ import annotations

from typing import Any

import pytest
from match_harness import Harness
from match_service.application.owner import plan_error
from match_service.domain.errors import MatchInvalidError, override_details
from match_service.infrastructure.rooms.codec import dumps, loads

from gv_core.errors import InvalidInputError

pytestmark = pytest.mark.unit

KEY = "music.lobby.classic"
REVERSE = {"type": "audio_reverse", "params": {"start_s": 0, "levels": [{"seconds": 15}]}}


def harness(*, formats: list[str] | None = None, clue: bool = True) -> Harness:
    h = Harness()
    over = h.bodies[KEY]["session"]["lobby"]["overridable"]
    if clue:
        over["clue"] = [h.bodies[KEY]["clue"], REVERSE]
    if formats:
        over["answer_format"] = formats
    return h


async def test_template_lists_variants_in_room_options() -> None:
    h = harness(formats=["classic", "pick"])
    tpl = h.factory.template(KEY, 1, h.bodies[KEY])
    assert tpl.template.options is not None
    assert tpl.template.options.clue_type == ("audio_intro", "audio_reverse")
    assert tpl.template.options.answer_format == ("classic", "pick")


async def test_override_goes_to_plan_request_and_snapshot() -> None:
    h = harness()
    room, host = await h.room(KEY, clue_type="audio_reverse")
    await h.join(room, 1)
    await h.start(room, host)

    request = h.plans.requests[-1]
    assert request.clue_type == "audio_reverse" and request.answer_format is None
    assert room.match is not None and room.match.body["clue"]["type"] == "audio_reverse"
    restored = loads(dumps(room))
    assert restored.settings.clue_type == "audio_reverse"


async def test_defaults_send_no_overrides() -> None:
    h = harness()
    room, host = await h.room(KEY)
    await h.join(room, 1)
    await h.start(room, host)

    request = h.plans.requests[-1]
    assert request.clue_type is None and request.answer_format is None


@pytest.mark.parametrize(
    ("field", "value"), [("clue_type", "soundtrack"), ("answer_format", "pick")]
)
async def test_value_outside_template_options_is_refused(field: str, value: str) -> None:
    h = harness()
    with pytest.raises(MatchInvalidError) as info:
        await h.room(KEY, **{field: value})
    assert info.value.code == "match.setting_not_allowed"
    assert info.value.details == {"field": field}


async def test_template_without_variants_refuses_any_override() -> None:
    h = Harness()
    with pytest.raises(MatchInvalidError):
        await h.room(KEY, clue_type="audio_reverse")


async def test_incompatible_combination_keeps_lobby_with_reason() -> None:
    """Сочетание отвергает валидатор ядра в game (422 ``game.override_invalid``): матч не
    стартует, комната остаётся в лобби, хост видит причину."""
    h = harness(formats=["classic", "pick"])
    room, host = await h.room(KEY, answer_format="pick")
    await h.join(room, 1)
    h.plans.fail(
        InvalidInputError(
            "x",
            code="game.override_invalid",
            details={"errors": [{"path": "session.contest.type", "code": "config.x"}]},
        )
    )

    reply = await h.cmd(room, host, "start", expected_version=room.version)

    assert reply["status"] == 422 and reply["code"] == "match.setting_not_allowed"
    assert room.phase.value == "lobby" and room.start_error is not None
    assert room.start_error[0] == "match.setting_not_allowed"


def test_game_errors_map_to_setting_not_allowed_in_match_terms() -> None:
    err = plan_error(
        InvalidInputError("x", code="game.override_not_allowed", details={"field": "clue"})
    )
    assert err.code == "match.setting_not_allowed"
    assert err.details == {"field": "clue_type", "reason": "not_allowed"}
    assert override_details("game.override_invalid", {"errors": [1]}) == {
        "field": "overrides",
        "reason": "incompatible",
        "errors": [1],
    }


def test_request_defaults_are_backward_compatible() -> None:
    from match_service.application.ports import RoundPlanRequest

    request: Any = RoundPlanRequest(mode_key=KEY, version=1, seed="a" * 32, rounds=1)
    assert request.clue_type is None and request.answer_format is None
