"""params_hash ядра совпадает с media-pipeline по общим golden-векторам (D7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gv_game_kernel import canonical_json, params_hash

pytestmark = pytest.mark.contract

GOLDEN = Path(__file__).resolve().parents[3] / "contracts" / "clues" / "params_hash.golden.json"
VECTORS: list[dict[str, Any]] = json.loads(GOLDEN.read_text(encoding="utf-8"))["vectors"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_matches_golden(vector: dict[str, Any]) -> None:
    assert canonical_json(vector["params"]) == vector["canonical_json"]
    assert params_hash(vector["params"]) == vector["params_hash"]


def test_golden_covers_media_profiles() -> None:
    names = {v["name"] for v in VECTORS}
    assert {"profile:audio_intro:default", "profile:frame_progressive:default"} <= names


@pytest.mark.parametrize(("value", "error"), [(float("nan"), ValueError), (object(), TypeError)])
def test_rejects_non_json(value: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        params_hash({"x": value})


def test_key_order_and_integral_floats_do_not_matter() -> None:
    assert params_hash({"a": 1, "b": [1.0, (2, 3)]}) == params_hash({"b": [1, [2, 3]], "a": 1.0})
