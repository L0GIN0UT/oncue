"""YAML кураторского сборника: разбор, вывод с подписями, ошибки сервиса с номерами строк."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from admin_service.apps.collections import collection_text
from admin_service.apps.game_config.config_text import ConfigTextError

pytestmark = pytest.mark.unit

A, B = str(uuid4()), str(uuid4())


def _detail(**kw: Any) -> dict[str, Any]:
    return {
        "domain": "film",
        "title": "Классика",
        "description": None,
        "tags": ["classic"],
        "cover": {"preset": "night"},
        "default_mode_key": "film.collection.frame",
        "visibility": "public",
        "items": [
            {
                "position": 1,
                "media_item_id": B,
                "title": "Титаник",
                "year": 1997,
                "available": True,
                "custom": {"emoji": "🚢🧊💔🎻🌊🚪"},
            },
            {
                "position": 0,
                "media_item_id": A,
                "title": "Сталкер\n# не комментарий",
                "year": 1979,
                "available": True,
                "custom": None,
            },
        ],
    } | kw


def test_dump_parse_round_trip_keeps_order_and_emoji() -> None:
    text = collection_text.dump(_detail())
    body = collection_text.parse(text)
    assert body == {
        "domain": "film",
        "title": "Классика",
        "tags": ["classic"],
        "cover": {"preset": "night"},
        "default_mode_key": "film.collection.frame",
        "visibility": "public",
        "items": [
            {"media_item_id": A},
            {"media_item_id": B, "custom": {"emoji": "🚢🧊💔🎻🌊🚪"}},
        ],
    }
    assert body == collection_text.body_of(_detail())
    # Подпись — комментарий одной строкой: перевод строки в названии не ломает YAML.
    assert f"- {A}  # Сталкер # не комментарий (1979)" in text


def test_string_item_is_shorthand_and_uuid_is_canonical() -> None:
    text = f"title: x\nitems:\n  - {A.upper()}\n"
    assert collection_text.parse(text)["items"] == [{"media_item_id": A}]


@pytest.mark.parametrize(
    ("text", "line", "fragment"),
    [
        ("title: x\nreason: y\nitems: []\n", 2, "полем формы"),
        ("title: x\nitems:\n  - не-uuid\n", 3, "UUID"),
        (f"title: x\nitems:\n  - {A}\n  - media_item_id: {B}\n    extra: 1\n", 5, "неизвестное"),
        (f"title: x\nitems:\n  - media_item_id: {A}\n    custom: {{emoji: 5}}\n", 4, "строка"),
        ("title: x\nitems: {}\n", 2, "список"),
    ],
)
def test_parse_errors_point_to_line(text: str, line: int, fragment: str) -> None:
    with pytest.raises(ConfigTextError) as exc:
        collection_text.parse(text)
    assert exc.value.line == line
    assert fragment in exc.value.message


def test_service_errors_get_lines_and_human_text() -> None:
    text = collection_text.dump(_detail())
    rows = collection_text.error_rows(
        text,
        details={
            "errors": [
                {"path": "items[1].custom.emoji", "code": "emoji_too_short", "min": 6},
                {"path": "title", "code": "title_invalid", "reason": "ugc.denied_word"},
                {"path": "items", "code": "items_too_few", "min": 5},
                {"path": "x", "code": "brand_new_code"},
            ]
        },
        errors=[{"loc": ["body", "items", 0, "media_item_id"], "msg": "bad", "type": "uuid"}],
    )
    lines = text.splitlines()
    by_path = {r["path"]: r for r in rows}
    assert by_path["items[1].custom.emoji"]["message"] == "эмодзи меньше 6"
    assert "custom" in lines[by_path["items[1].custom.emoji"]["line"] - 1]
    assert by_path["title"]["message"] == "название не подходит: запрещённое слово"
    assert by_path["items"]["message"] == "единиц меньше 5"
    assert by_path["x"]["message"] == "brand_new_code"
    assert A in lines[by_path["items[0].media_item_id"]["line"] - 1]


def test_template_is_yaml_for_domain() -> None:
    text = collection_text.template("music")
    assert "domain: music" in text
    assert "music.collection." in text
