"""Настройки library этапа 3b (план §2.8): умолчания и env."""

from __future__ import annotations

from pathlib import Path

import pytest
from library_service.settings import Settings

pytestmark = pytest.mark.unit


def test_defaults_match_plan() -> None:
    settings = Settings()
    assert settings.library.shelves_config is None
    assert settings.library.limits.custom_shelves_max == 50
    assert settings.ugc.denylist_path is None


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GV_LIBRARY__SHELVES_CONFIG", "/etc/gv/shelves.yaml")
    monkeypatch.setenv("GV_LIBRARY__LIMITS__SAVES_PER_MIN", "10")
    settings = Settings()
    assert settings.library.shelves_config == Path("/etc/gv/shelves.yaml")
    assert settings.library.limits.saves_per_min == 10
    monkeypatch.setenv("GV_LIBRARY__SHELVES_CONFIG", "")
    assert Settings().library.shelves_config is None
