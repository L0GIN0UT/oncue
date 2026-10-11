"""Политика бакета ассетов: gateway читает только ``a/*`` (ADR 0012, ADR 0021)."""

from __future__ import annotations

import json

import pytest
from media_pipeline_service.domain.links import ASSET_PREFIX
from media_pipeline_service.infrastructure.storage import assets_read_policy

pytestmark = pytest.mark.unit


def test_policy_allows_only_anonymous_get_of_asset_prefix() -> None:
    policy = json.loads(assets_read_policy("gv-assets"))
    assert len(policy["Statement"]) == 1
    statement = policy["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == "*"
    assert statement["Action"] == ["s3:GetObject"]  # ни записи, ни удаления, ни листинга
    assert statement["Resource"] == [f"arn:aws:s3:::gv-assets/{ASSET_PREFIX}*"]


def test_policy_is_deterministic() -> None:
    """Одинаковый текст при каждом выкате: повторный ``migrate`` ничего не меняет."""
    assert assets_read_policy("b") == assets_read_policy("b")
