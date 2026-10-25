"""Редактор находок формой: тело <-> поля, сохранение чужих полей, проверка, доли клика."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from admin_service.apps.events import finds_form as ff
from hypothesis import given
from hypothesis import strategies as st

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[4] / "contracts" / "progression"
BODY = json.loads((_ROOT / "examples" / "event_definition.v1.json").read_text("utf-8"))
SCHEMA = json.loads((_ROOT / "event_definition.v1.json").read_text("utf-8"))


def _post(body: dict[str, Any]) -> dict[str, str]:
    header = ff.header_from_body(body)
    rows = ff.rows_from_body(body)
    data = {f"h_{k}": v for k, v in header.items()} | {"find_count": str(len(rows))}
    for i, row in enumerate(rows):
        for name in ff.ROW_FIELDS:
            data[f"f{i}_{name}"] = str(row[name])
        if row["final"]:
            data["final_index"] = str(i)
    return data


def _roundtrip(body: dict[str, Any]) -> dict[str, Any]:
    header, rows = ff.parse_post(_post(body))
    return ff.build_body(body, header, rows)


def test_roundtrip_of_contract_example_is_lossless() -> None:
    assert _roundtrip(BODY) == BODY


def test_unknown_fields_are_preserved() -> None:
    body = copy.deepcopy(BODY)
    body["future_top"] = {"a": 1}
    body["finds"][0]["future_find"] = "x"
    body["finds"][0]["reward"]["future_reward"] = 7
    body["finds"][1]["trigger"]["future_trigger"] = True
    body["finds"][2]["trigger"]["params"]["future_param"] = 5
    assert _roundtrip(body) == body


def test_changing_trigger_type_drops_old_trigger_fields() -> None:
    body = copy.deepcopy(BODY)
    data = _post(body)
    data["f1_trigger_type"] = "ui_spot"
    data["f1_page_key"] = "shop"
    header, rows = ff.parse_post(data)
    built = ff.build_body(body, header, rows)
    assert built["finds"][1]["trigger"] == {"type": "ui_spot", "page_key": "shop"}


def test_final_find_goes_last_and_numbers_follow_order() -> None:
    data = _post(BODY)
    data["final_index"] = "0"
    header, rows = ff.parse_post(data)
    built = ff.build_body(BODY, header, rows)
    assert [f["number"] for f in built["finds"]] == [1, 2, 3, 4]
    assert built["finds"][-1]["key"] == "first_leaf"
    assert [f["final"] for f in built["finds"]] == [False, False, False, True]


def test_ops_add_delete_reorder() -> None:
    rows = ff.rows_from_body(BODY)
    keys = lambda r: [x["key"] for x in r]  # noqa: E731
    assert keys(ff.apply_op(rows, "up:1"))[:2] == ["duel_trail", "first_leaf"]
    assert keys(ff.apply_op(rows, "down:0"))[:2] == ["duel_trail", "first_leaf"]
    assert len(ff.apply_op(rows, "del:0")) == len(rows) - 1
    assert len(ff.apply_op(rows, "add")) == len(rows) + 1
    assert ff.apply_op(rows, "up:0") == rows
    assert ff.apply_op(rows, "del:99") == rows
    assert ff.apply_op(rows, "garbage") == rows


def test_add_respects_max_finds() -> None:
    rows = [ff.blank_row(i) for i in range(ff.MAX_FINDS)]
    assert len(ff.apply_op(rows, "add")) == ff.MAX_FINDS


def test_valid_example_has_no_errors() -> None:
    header, rows = ff.parse_post(_post(BODY))
    assert ff.validate(header, rows) == {}


def _errors(mutate: Any) -> dict[str, str]:
    data = _post(BODY)
    mutate(data)
    return ff.validate(*ff.parse_post(data))


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        ({"f0_key": "Bad Key"}, "f0_key"),
        ({"f1_key": "first_leaf"}, "f1_key"),
        ({"f0_x_pct": "101"}, "f0_x_pct"),
        ({"f0_y_pct": "abc"}, "f0_y_pct"),
        ({"f0_xp": "5001"}, "f0_xp"),
        ({"f0_verses": "-1"}, "f0_verses"),
        ({"f0_title": ""}, "f0_title"),
        ({"f0_page_key": ""}, "f0_page_key"),
        ({"f1_metric": "nope"}, "f1_metric"),
        ({"f1_target": "0"}, "f1_target"),
        ({"f1_place_max": "3"}, "f1_place_max"),
        ({"f1_slot": "frame"}, "f1_slot"),
        ({"f2_collection_keys": "not-a-uuid"}, "f2_collection_keys"),
        ({"f2_collection_tags": "Bad Tag"}, "f2_collection_tags"),
        ({"f1_collection_tags": "ok_tag"}, "f1_collection_keys"),
        ({"h_title": ""}, "h_title"),
        ({"h_theme_key": "Bad"}, "h_theme_key"),
    ],
)
def test_field_validation(patch: dict[str, str], field: str) -> None:
    assert field in _errors(lambda d: d.update(patch))


def test_weekly_top_place_requires_place_max_and_item_equipped_one_of() -> None:
    def weekly(d: dict[str, str]) -> None:
        d.update({"f1_metric": "weekly_top_place", "f1_place_max": ""})

    def equipped(d: dict[str, str]) -> None:
        d.update(
            {"f1_metric": "item_equipped", "f1_target": "1", "f1_slot": "a", "f1_item_key": "b"}
        )

    assert "f1_place_max" in _errors(weekly)
    assert "f1_slot" in _errors(equipped)


def test_exactly_one_final_and_find_count() -> None:
    assert "finds" in _errors(lambda d: d.pop("final_index"))
    data = _post(BODY)
    rows = ff.parse_post(data)[1][:1]
    assert "finds" in ff.validate(ff.parse_post(data)[0], rows)


def test_server_error_path_maps_to_form_field_with_final_reorder() -> None:
    order = ff.sent_order([{"final": True}, {"final": False}])  # type: ignore[list-item]
    assert order == [1, 0]
    assert ff.server_field("finds.0.x_pct", order) == "f1_x_pct"
    assert ff.server_field("finds[1].trigger.params.place_max", order) == "f0_place_max"
    assert ff.server_field("finds[0].trigger.target", order) == "f1_target"
    assert ff.server_field("finds[0].reward.xp", order) == "f1_xp"
    assert ff.server_field("title", order) == "h_title"
    assert ff.server_field("finds", order) is None
    assert ff.server_field("finds.9.key", order) is None


def test_enums_match_contract() -> None:
    defs = SCHEMA["$defs"]
    server = defs["server_trigger"]["properties"]
    assert set(ff.METRICS) == set(server["metric"]["enum"])
    assert set(ff.PERIODS) == set(server["period"]["enum"])
    assert set(ff.DOMAINS) == set(server["params"]["properties"]["domain"]["enum"])
    assert set(ff.PLAY_PAGES) == set(defs["find"]["properties"]["play_page"]["enum"])
    assert set(ff.SPOT_PAGES) == set(defs["ui_spot_trigger"]["properties"]["page_key"]["enum"])


def test_pct_from_click_examples() -> None:
    assert ff.pct_from_click(160, 640) == 25.0
    assert ff.pct_from_click(-5, 640) == 0.0
    assert ff.pct_from_click(700, 640) == 100.0
    assert ff.pct_from_click(1, 3) == 33.3
    assert ff.pct_from_click(10, 0) == 0.0


@given(st.floats(-1e4, 1e4), st.floats(1, 1e4))
def test_pct_from_click_is_bounded_and_monotonic(offset: float, size: float) -> None:
    value = ff.pct_from_click(offset, size)
    assert 0.0 <= value <= 100.0
    assert ff.pct_from_click(offset + 1, size) >= value
