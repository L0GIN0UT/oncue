"""TTL кэша конфига по умолчанию короткий: публикация доходит до реплик быстро (ADR 0053)."""

from __future__ import annotations

import pytest
from social_service.settings import SocialSettings

pytestmark = pytest.mark.unit

MAX_DEFAULT_TTL_S = 30


def test_default_config_cache_ttl_is_short() -> None:
    assert 0 < SocialSettings().config_cache_ttl_s <= MAX_DEFAULT_TTL_S
