"""Конфиг social: копия схемы в пакете, сид и пример контракта согласованы (ADR 0053)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest
import yaml
from social_service.domain.config import SocialConfig
from social_service.infrastructure.config_validation import validate_config_body

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "contracts" / "social"


def test_packaged_schema_equals_contract() -> None:
    packaged = resources.files("social_service.config").joinpath("social_config.schema.json")
    expected = json.loads((CONTRACT / "social_config.v1.json").read_text("utf-8"))
    assert json.loads(packaged.read_text("utf-8")) == expected


def test_seed_equals_contract_example_and_is_valid() -> None:
    seed = yaml.safe_load((ROOT / "seeds" / "social" / "config.yaml").read_text("utf-8"))
    example = json.loads((CONTRACT / "examples" / "social_config.v1.json").read_text("utf-8"))
    assert seed == example
    validate_config_body(seed)
    config = SocialConfig.from_body(1, seed)
    assert config.presence.ttl_s > config.presence.heartbeat_s
