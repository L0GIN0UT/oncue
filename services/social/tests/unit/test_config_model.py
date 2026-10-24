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
