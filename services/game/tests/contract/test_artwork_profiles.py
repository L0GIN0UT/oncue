"""Профили картинки ответа в game ↔ профили media-pipeline (golden-хэши, ADR 0073)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from game_service.application.artwork import ARTWORK_CLUE_TYPE, ArtworkProfiles
from game_service.settings import GameSettings
from jsonschema import Draft202012Validator

from gv_testkit.contracts import contracts_dir

pytestmark = pytest.mark.contract

# Домен game -> имя профиля media-pipeline в golden-файле.
GOLDEN_NAME = {"music": "profile:artwork:default", "film": "profile:artwork:film"}


def golden() -> dict[str, dict[str, Any]]:
    path = contracts_dir() / "clues" / "params_hash.golden.json"
    return {v["name"]: v for v in json.loads(path.read_text(encoding="utf-8"))["vectors"]}


def test_default_profiles_match_media_golden_hashes() -> None:
    profiles = GameSettings().artwork_profiles
    assert set(profiles) == set(GOLDEN_NAME)
    policy = ArtworkProfiles(profiles)
    for domain, name in GOLDEN_NAME.items():
        assert policy.params_hash(domain) == golden()[name]["params_hash"], domain
        assert profiles[domain] == golden()[name]["params"], domain


def test_default_profiles_match_clue_contract() -> None:
    path = contracts_dir() / "clues" / f"{ARTWORK_CLUE_TYPE}.params.v1.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for domain, params in GameSettings().artwork_profiles.items():
        assert not list(validator.iter_errors(params)), domain


def test_unknown_domain_has_no_artwork_profile() -> None:
    assert ArtworkProfiles(GameSettings().artwork_profiles).params_hash("book") is None
    assert ArtworkProfiles({}).params_hash("music") is None
