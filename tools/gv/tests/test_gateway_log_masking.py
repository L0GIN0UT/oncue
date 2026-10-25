"""Страж: access-лог gateway не содержит query (токен отписки, md5 и expires ссылок).

Секреты приходят в query: одноразовый токен отписки (``POST /v1/unsubscribe?token=…``), подпись
``md5`` ссылок ``/media/``. Поэтому в ``log_format`` пишется только ``$uri`` без query; сырой
``$request_uri``, ``$request``, ``$args`` и ``$query_string`` в формат попадать не должны.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gv_cli.registry import repo_root

pytestmark = pytest.mark.unit

CONF = (repo_root(Path(__file__)) / "infra/nginx/nginx.conf").read_text(encoding="utf-8")


def _log_format() -> str:
    found = re.search(r"log_format\s+\w+\s+(?:escape=\w+\s+)?(.*?);", CONF, re.DOTALL)
    assert found, "log_format не найден"
    return found[1]


@pytest.mark.parametrize(
    "raw", [r"\$request_uri", r"\$args", r"\$query_string", r"\$is_args", r"\$request\b"]
)
def test_log_format_has_no_query_variables(raw: str) -> None:
    assert not re.search(raw, _log_format()), raw


def test_log_format_uses_uri_without_query() -> None:
    assert "$uri" in _log_format()
