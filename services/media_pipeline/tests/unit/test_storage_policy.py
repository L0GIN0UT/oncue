"""Политика бакета: анонимно читаются только ``a/*``, ``u/*``; ``p/*`` закрыт (ADR 0061)."""

from __future__ import annotations

import json

import pytest
from media_pipeline_service.domain.links import ASSET_PREFIX
from media_pipeline_service.domain.ugc import PENDING_PREFIX
from media_pipeline_service.infrastructure.storage import assets_read_policy

pytestmark = pytest.mark.unit


def test_policy_allows_only_anonymous_get_of_asset_prefix() -> None:
    policy = json.loads(assets_read_policy("gv-assets"))
    assert len(policy["Statement"]) == 1
    statement = policy["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert statement["Principal"] == "*"
    assert statement["Action"] == ["s3:GetObject"]  # ни записи, ни удаления, ни листинга
    # a/ - ассеты загадок, u/ - одобренные UGC (публично через /ugc/); вне этих префиксов
    # читать нечего. p/ (UGC на модерации) анонимно не читается: его отдаёт media-pipeline.
    assert statement["Resource"] == [
        f"arn:aws:s3:::gv-assets/{prefix}*" for prefix in (ASSET_PREFIX, "u/")
    ]


def test_policy_does_not_expose_pending_prefix() -> None:
    text = assets_read_policy("gv-assets")
    assert f"/{PENDING_PREFIX}" not in text
    resources = json.loads(text)["Statement"][0]["Resource"]
    assert not any(PENDING_PREFIX in r.split("/", 1)[1] for r in resources)


def test_policy_is_deterministic() -> None:
    """Одинаковый текст при каждом выкате: повторный ``migrate`` ничего не меняет."""
    assert assets_read_policy("b") == assets_read_policy("b")
