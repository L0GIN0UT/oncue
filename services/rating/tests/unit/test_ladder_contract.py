"""Контракт конфига ladder-а (ADR 0037): схема валидна, пример проходит, ошибки ловятся."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.unit

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "rating"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    return data


def _validator() -> Draft202012Validator:
    schema = _load("ladder.v1.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_example_ladder_is_valid() -> None:
    assert list(_validator().iter_errors(_load("examples/ladder.film.v1.json"))) == []


def test_example_tiers_ascend_and_end_open() -> None:
    bounds = [t["below"] for t in _load("examples/ladder.film.v1.json")["tiers"]]
    assert bounds[-1] is None
    assert bounds[:-1] == sorted(set(bounds[:-1]))


def _drop_start(c: dict[str, Any]) -> None:
    del c["elo"]["start"]


def _party_multiplier(c: dict[str, Any]) -> None:
    c["elo"]["format_multiplier"]["party"] = 1.0


def _zero_multiplier(c: dict[str, Any]) -> None:
    c["elo"]["format_multiplier"]["lite"] = 0


def _drop_window_max(c: dict[str, Any]) -> None:
    del c["matchmaking"]["window"]["max"]


def _bad_domain(c: dict[str, Any]) -> None:
    c["domain"] = "anime"


def _extra_field(c: dict[str, Any]) -> None:
    c["surprise"] = 1


@pytest.mark.parametrize(
    "patch",
    [_drop_start, _party_multiplier, _zero_multiplier, _drop_window_max, _bad_domain, _extra_field],
)
def test_broken_ladder_is_rejected(patch: Callable[[dict[str, Any]], None]) -> None:
    config = copy.deepcopy(_load("examples/ladder.film.v1.json"))
    patch(config)
    assert list(_validator().iter_errors(config)) != []
