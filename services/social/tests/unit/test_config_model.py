"""Модель конфига social строится из примера контракта (ADR 0053)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from social_service.domain.config import SocialConfig

pytestmark = pytest.mark.unit

EXAMPLE = Path(__file__).resolve().parents[4] / "contracts/social/examples/social_config.v1.json"


def test_from_body_builds_config_from_contract_example() -> None:
    config = SocialConfig.from_body(3, json.loads(EXAMPLE.read_text(encoding="utf-8")))
    assert config.version == 3
    assert config.comments.edit_window_s == 300
    assert config.reports.threshold_per_day == 3
    reason = config.reports.reason("cheating")
    assert reason is not None
    assert reason.target_kinds == {"player"}
    assert config.reports.reason("nope") is None


def _body() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_friends_windows_default_when_absent_in_old_config() -> None:
    """Старые версии конфига без окон остаются валидными: умолчания прежние."""
    body = _body()
    body["friends"].pop("lookup_window_s", None)
    body["friends"].pop("invite_window_s", None)
    friends = SocialConfig.from_body(1, body).friends
    assert (friends.lookup_window_s, friends.invite_window_s) == (3600, 60)


def test_friends_windows_taken_from_config() -> None:
    body = _body()
    body["friends"].update(lookup_window_s=7200, invite_window_s=30)
    friends = SocialConfig.from_body(1, body).friends
    assert (friends.lookup_window_s, friends.invite_window_s) == (7200, 30)


def test_schema_accepts_old_and_new_friends_windows() -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(EXAMPLE.parents[1].joinpath("social_config.v1.json").read_text("utf-8"))
    validator = Draft202012Validator(schema)
    old = _body()
    old["friends"].pop("lookup_window_s", None)
    old["friends"].pop("invite_window_s", None)
    validator.validate(old)
    new = _body()
    new["friends"].update(lookup_window_s=7200, invite_window_s=30)
    validator.validate(new)
    new["friends"]["lookup_window_s"] = 0
    assert not validator.is_valid(new)


@pytest.mark.parametrize(
    ("field", "bad", "good"),
    [
        ("lookup_window_s", [0, 599, 86401, 10**6], [600, 3600, 86400]),
        ("invite_window_s", [0, 9, 3601, 10**6], [10, 60, 3600]),
    ],
)
def test_schema_bounds_of_friends_windows(field: str, bad: list[int], good: list[int]) -> None:
    from jsonschema import Draft202012Validator

    schema = json.loads(EXAMPLE.parents[1].joinpath("social_config.v1.json").read_text("utf-8"))
    validator = Draft202012Validator(schema)
    for value in bad:
        body = _body()
        body["friends"][field] = value
        assert not validator.is_valid(body), value
    for value in good:
        body = _body()
        body["friends"][field] = value
        assert validator.is_valid(body), value
