"""Страж: лимит тела запроса gateway согласован с лимитом аватарки progression.

nginx режет тело раньше сервиса и отвечает 413 своей страницей; если лимит location
аватарки меньше ``GV_PROGRESSION__AVATAR_MAX_BYTES`` плюс служебные части multipart,
валидная загрузка получит 413 от gateway. Текст 413 gateway - Problem JSON, как у 404.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from progression_service.settings import ProgressionSettings

from gv_cli.registry import repo_root

pytestmark = pytest.mark.unit

ROOT = repo_root(Path(__file__))
CONF = (ROOT / "infra/nginx/nginx.conf").read_text(encoding="utf-8")
AVATAR_PATH = "/api/progression/v1/me/avatar"
# Служебные части multipart (граница, заголовки части, имя файла, завершающая граница) -
# сотни байт; берём запас с большим зазором. Лимит файла - в настройках progression.
MULTIPART_OVERHEAD = 64 * 1024
_UNITS = {"": 1, "k": 1024, "m": 1024 * 1024, "g": 1024**3}


def _bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+)([kmg]?)", value.lower())
    assert match, f"не размер nginx: {value!r}"
    return int(match[1]) * _UNITS[match[2]]


def _block(source: str, header: str) -> str:
    """Тело блока ``header { ... }`` (с учётом вложенных скобок)."""
    start = source.index(header)
    depth, pos = 0, source.index("{", start)
    begin = pos + 1
    while True:
        char = source[pos]
        depth += char == "{"
        depth -= char == "}"
        if depth == 0:
            return source[begin:pos]
        pos += 1


def _limit(source: str) -> int:
    found = re.search(r"^\s*client_max_body_size\s+(\w+)\s*;", source, re.MULTILINE)
    assert found, "client_max_body_size не задан"
    return _bytes(found[1])


def test_avatar_location_limit_covers_avatar_max_bytes_with_headroom() -> None:
    avatar_max = ProgressionSettings.model_fields["avatar_max_bytes"]
    limit_max = next(m.le for m in avatar_max.metadata if hasattr(m, "le"))
    default = ProgressionSettings().avatar_max_bytes
    location = _block(CONF, f"location = {AVATAR_PATH}")
    # Верхняя граница настройки (le), а не только значение по умолчанию.
    assert _limit(location) >= max(default, limit_max) + MULTIPART_OVERHEAD


def test_avatar_location_proxies_to_progression_like_generated_route() -> None:
    location = _block(CONF, f"location = {AVATAR_PATH}")
    assert "proxy_pass http://gv_progression/v1/me/avatar;" in location
    assert "limit-api.inc" in location
    assert "snippets/proxy.conf" in location


def test_other_routes_keep_small_default_limit() -> None:
    http_level = CONF.replace(_block(CONF, f"location = {AVATAR_PATH}"), "")
    assert _limit(http_level) <= 2 * 1024 * 1024


def test_payload_too_large_is_problem_json() -> None:
    assert re.search(r"error_page\s+413\s+=\s*@payload_too_large\s*;", CONF)
    location = _block(CONF, "location @payload_too_large")
    assert "application/problem+json" in location
    body = re.search(r"return 413 '(\{.*\})';", location)
    assert body, "ответ 413 должен быть JSON"
    problem = json.loads(body[1])
    assert problem["status"] == 413 and problem["code"] == "request.payload_too_large"
