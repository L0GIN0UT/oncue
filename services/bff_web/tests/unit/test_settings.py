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


def test_social_settings_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GV_PAGES__COMMENTS_LIMIT", raising=False)
    monkeypatch.delenv("GV_UPSTREAMS__SOCIAL_URL", raising=False)
    default = Settings(environment="ci")
    assert default.pages.comments_limit == 30
    assert default.upstreams.social_url == "http://social:8000"

    monkeypatch.setenv("GV_PAGES__COMMENTS_LIMIT", "7")
    monkeypatch.setenv("GV_UPSTREAMS__SOCIAL_URL", "http://social.internal:9000")
    custom = Settings(environment="ci")
    assert custom.pages.comments_limit == 7
    assert custom.upstreams.social_url == "http://social.internal:9000"


@pytest.mark.parametrize("bad", ["0", "101"])
def test_comments_limit_is_bounded_by_source_limit(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("GV_PAGES__COMMENTS_LIMIT", bad)
    with pytest.raises(ValidationError):
        Settings(environment="ci")


@pytest.mark.parametrize("bad", ["Bad-Key", "", "a" * 65, "1abc", "with space"])
def test_invalid_theme_key_fails_settings_validation(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv(ENV, bad)
    with pytest.raises(ValidationError):
        Settings(environment="ci")
