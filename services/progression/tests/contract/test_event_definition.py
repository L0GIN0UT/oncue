"""Контракт определения события 5d: пример, границы схемы и Pydantic-модель тела совпадают."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from progression_service.api.admin.events_schemas import EventDefinition
from pydantic import ValidationError

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[4] / "contracts" / "progression"
SCHEMA = json.loads((ROOT / "event_definition.v1.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((ROOT / "examples" / "event_definition.v1.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)


def mutated(change: Any) -> dict[str, Any]:
    data = copy.deepcopy(EXAMPLE)
    change(data)
    return data  # type: ignore[no-any-return]  # deepcopy возвращает Any


def _second_final(d: dict[str, Any]) -> None:
    d["finds"][0]["final"] = True


def _unknown_metric(d: dict[str, Any]) -> None:
    d["finds"][1]["trigger"]["metric"] = "unknown_metric"


def _bad_page(d: dict[str, Any]) -> None:
    d["finds"][0]["trigger"]["page_key"] = "admin"


def _x_over(d: dict[str, Any]) -> None:
    d["finds"][0]["x_pct"] = 100.5


def _extra_field(d: dict[str, Any]) -> None:
    d["finds"][0]["secret"] = 1


def _one_find(d: dict[str, Any]) -> None:
    d["finds"] = d["finds"][:1]


COLL = "0190a000-0000-7000-8000-00000000000a"


def _keys(d: dict[str, Any], metric: str, keys: Any) -> None:
    d["finds"][1]["trigger"].update(metric=metric, params={"collection_keys": keys})


def _keys_other_metric(d: dict[str, Any]) -> None:
    _keys(d, "rounds_played", [COLL])


def _keys_not_uuid(d: dict[str, Any]) -> None:
    _keys(d, "collection_played", ["not-a-uuid"])


def _keys_duplicates(d: dict[str, Any]) -> None:
    _keys(d, "collection_played", [COLL, COLL])


def _keys_too_many(d: dict[str, Any]) -> None:
    _keys(d, "collection_played", [f"0190a000-0000-7000-8000-{i:012d}" for i in range(21)])


@pytest.mark.parametrize("keys", [[COLL], []])
def test_collection_keys_accepted_for_collection_played(keys: list[str]) -> None:
    data = mutated(lambda d: _keys(d, "collection_played", keys))
    assert list(VALIDATOR.iter_errors(data)) == []
    EventDefinition.model_validate(data)


def test_example_is_valid_and_covers_variants() -> None:
    assert list(VALIDATOR.iter_errors(EXAMPLE)) == []
    finds = EXAMPLE["finds"]
    assert len(finds) == 4
    assert sum(f["final"] for f in finds) == 1
    assert {f["trigger"]["type"] for f in finds} == {"server", "ui_spot"}


def test_pydantic_body_accepts_example_and_round_trips() -> None:
    body = EventDefinition.model_validate(EXAMPLE)
    dumped = body.model_dump(mode="json", exclude_none=True)
    assert list(VALIDATOR.iter_errors(dumped)) == []


@pytest.mark.parametrize(
    "change",
    [
        _second_final,
        _unknown_metric,
        _bad_page,
        _x_over,
        _extra_field,
        _one_find,
        _keys_other_metric,
        _keys_not_uuid,
        _keys_duplicates,
        _keys_too_many,
    ],
)
def test_schema_and_model_reject_the_same_invalid_bodies(change: Any) -> None:
    data = mutated(change)
    assert list(VALIDATOR.iter_errors(data)) != []
    with pytest.raises(ValidationError):
        EventDefinition.model_validate(data)


def test_no_final_find_is_rejected() -> None:
    data = mutated(lambda d: d["finds"][3].update(final=False))
    assert list(VALIDATOR.iter_errors(data)) != []
    with pytest.raises(ValidationError):
        EventDefinition.model_validate(data)
