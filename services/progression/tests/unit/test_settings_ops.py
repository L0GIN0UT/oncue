"""Границы эксплуатационных настроек сверки и ретенции (``GV_PROGRESSION__*``)."""

from __future__ import annotations

import pytest
from progression_service.settings import ProgressionSettings
from pydantic import ValidationError

pytestmark = pytest.mark.unit


def test_defaults_are_safe() -> None:
    s = ProgressionSettings()
    assert s.reconcile_full_every_s == 86_400  # полный проход раз в сутки
    assert s.reconcile_full_every_s >= s.reconcile_interval_s
    assert s.retention_metric_marks_days >= 14
    assert s.retention_quest_progress_days >= 14
    assert s.retention_batch >= 1


@pytest.mark.parametrize(
    "patch",
    [
        {"reconcile_full_every_s": 0},
        {"reconcile_full_every_s": 100.0, "reconcile_interval_s": 300.0},  # реже, чем тик
        {"reconcile_lag_s": 0},
        {"reconcile_lag_s": 100_000},
        {"reconcile_batch": 0},
        {"retention_metric_marks_days": 1},  # короче окна повторной доставки событий
        {"retention_quest_progress_days": 3},
        {"retention_metric_marks_days": 100_000},
        {"retention_batch": 0},
        {"retention_batch": 10_000_000},
        {"retention_pause_s": -1},
        {"retention_interval_s": 0},
    ],
)
def test_out_of_bounds_values_are_rejected(patch: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ProgressionSettings(**patch)  # type: ignore[arg-type]
