"""Контракты social 5c: события `social.social.v1` и конфиг (ADR 0050-0052)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from gv_auth import Sanction
from gv_testkit import assert_event_matches_schema, load_example
from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

PRODUCED = [
    "social.FriendRequested",
    "social.FriendAdded",
    "social.FriendRemoved",
    "social.UserBlocked",
    "social.RoomInviteSent",
    "social.CommentPosted",
    "social.CommentDeleted",
    "social.ReportFiled",
    "social.ReportResolved",
    "social.ContentModerated",
    "social.UserSanctionsChanged",
]
CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "social"


@pytest.mark.parametrize("event_type", PRODUCED)
def test_produced_examples_match_schema_and_topic(event_type: str) -> None:
    assert load_event_schema(event_type)["x-topic"] == "social.social.v1"
    assert_event_matches_schema(load_example(event_type))


@pytest.mark.parametrize(
    "event_type",
    ["social.ReportFiled", "social.ReportResolved", "social.ContentModerated"],
)
def test_moderation_events_do_not_carry_free_text(event_type: str) -> None:
    """Текст жалобы и заметки модератора (ПДн) в события не входят (ADR 0051)."""
    properties = load_event_schema(event_type)["properties"]
    assert not {"text", "note", "reason", "body"} & set(properties)


def test_sanctions_example_mask_matches_active_kinds() -> None:
    """Маска события = объединение битов активных санкций (ADR 0052); account в маску не входит."""
    payload = load_example("social.UserSanctionsChanged").payload
    bits = {
        "ranked": Sanction.RANKED,
        "chat": Sanction.CHAT_MUTE | Sanction.COMMENTS,
        "online": Sanction.ONLINE,
        "account": Sanction.NONE,
    }
    expected = Sanction.NONE
    for item in payload["active"]:
        expected |= bits[item["kind"]]
    assert payload["mask"] == int(expected)
    assert payload["account_banned"] == any(i["kind"] == "account" for i in payload["active"])


def test_online_bit_is_new_and_distinct() -> None:
    old = Sanction.RANKED | Sanction.CUSTOM_CONTENT | Sanction.CHAT_SLOW
    old |= Sanction.CHAT_MUTE | Sanction.COMMENTS
    assert Sanction.ONLINE == 32
    assert not old & Sanction.ONLINE


def _config_validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "social_config.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _example() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (CONTRACTS / "examples" / "social_config.v1.json").read_text(encoding="utf-8")
    )
    return data


def test_example_config_is_valid_and_follows_owner_decisions() -> None:
    config = _example()
    assert list(_config_validator().iter_errors(config)) == []
    assert config["comments"]["edit_window_s"] == 300
    assert config["comments"]["max_length"] == 500
    assert config["reports"]["text_max_length"] == 300


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("comments", "max_length"), 501),
        (("reports", "text_max_length"), 301),
        (("friends", "max_friends"), 0),
        (("sanctions", "min_duration_s"), 10),
        (("version",), 2),
    ],
)
def test_config_bounds_are_enforced(path: tuple[str, ...], value: int) -> None:
    config = copy.deepcopy(_example())
    node = config
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert list(_config_validator().iter_errors(config))


def test_config_rejects_unknown_key_and_missing_section() -> None:
    config = copy.deepcopy(_example())
    config["friends"]["extra"] = 1
    assert list(_config_validator().iter_errors(config))
    config = copy.deepcopy(_example())
    del config["sanctions"]
    assert list(_config_validator().iter_errors(config))
