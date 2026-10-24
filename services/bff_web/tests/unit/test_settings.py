"""Настройки bff_web: ключ темы оболочки (ADR 0049) берётся из окружения и проверяется."""

from __future__ import annotations

import pytest
from bff_web_service.settings import Settings
from pydantic import ValidationError

pytestmark = pytest.mark.unit

ENV = "GV_PAGES__DEFAULT_THEME_KEY"


def test_theme_key_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV, raising=False)
    assert Settings(environment="ci").pages.default_theme_key == "default"

    monkeypatch.setenv(ENV, "autumn_event")
    assert Settings(environment="ci").pages.default_theme_key == "autumn_event"


@pytest.mark.parametrize("bad", ["Bad-Key", "", "a" * 65, "1abc", "with space"])
def test_invalid_theme_key_fails_settings_validation(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv(ENV, bad)
    with pytest.raises(ValidationError):
        Settings(environment="ci")
