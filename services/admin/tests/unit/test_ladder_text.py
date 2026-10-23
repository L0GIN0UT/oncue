"""Пути ошибок rating (JSON Pointer) → пути для поиска строки в тексте редактора."""

from __future__ import annotations

import pytest
from admin_service.apps.rating import ladder_text

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [
        ("/elo/start", "elo.start"),
        ("/tiers/0/name", "tiers[0].name"),
        ("/tiers/2", "tiers[2]"),
        ("", ""),
        ("elo.start", "elo.start"),
        ("/a~1b/c", "a/b.c"),
    ],
)
def test_dotted(pointer: str, expected: str) -> None:
    assert ladder_text.dotted(pointer) == expected


def test_error_rows_get_line_numbers() -> None:
    text = "key: film\nelo:\n  start: много\n"
    rows = ladder_text.error_rows(
        text,
        details={"errors": [{"path": "/elo/start", "code": "type"}, "junk"]},
        errors=[{"loc": ["body", "config"], "msg": "bad", "type": "dict_type"}],
    )
    assert [(r["path"], r["line"]) for r in rows] == [("elo.start", 3), ("(корень)", 1)]
    assert rows[1]["message"] == "bad"
