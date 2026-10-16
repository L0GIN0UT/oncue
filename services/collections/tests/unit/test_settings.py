"""Настройки collections этапа 3b (план §2.8): умолчания, env и проверки согласованности."""

from __future__ import annotations

import pytest
from collections_service.settings import CollectionsLimits, DifficultySettings, Settings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_defaults_match_plan() -> None:
    limits = Settings().collections.limits
    assert (limits.drafts_max, limits.publish_per_day, limits.tags_max) == (20, 5, 5)
    assert (limits.items_min, limits.items_max) == (5, 200)
    assert Settings().ugc.denylist_path is None


def test_env_overrides_nested(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GV_COLLECTIONS__LIMITS__PUBLISH_PER_DAY", "3")
    monkeypatch.setenv("GV_COLLECTIONS__DIFFICULTY__THRESHOLDS", "[0.3,0.6]")
    monkeypatch.setenv("GV_UGC__DENYLIST_PATH", "")
    settings = Settings()
    assert settings.collections.limits.publish_per_day == 3
    assert settings.collections.difficulty.thresholds == (0.3, 0.6)
    assert settings.ugc.denylist_path is None


@pytest.mark.parametrize("thresholds", [(0.7, 0.4), (0.0, 0.5), (0.5, 1.0), (0.5, 0.5)])
def test_difficulty_thresholds_must_be_ordered(thresholds: tuple[float, float]) -> None:
    with pytest.raises(ValidationError):
        DifficultySettings(thresholds=thresholds)


def test_items_range_must_be_consistent() -> None:
    with pytest.raises(ValidationError):
        CollectionsLimits(items_min=10, items_max=5)
    with pytest.raises(ValidationError):
        CollectionsLimits(items_max=201)
