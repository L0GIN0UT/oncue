"""Текст конфига режима: разбор YAML/JSON, каноничный diff, строка по пути ошибки."""

from __future__ import annotations

import pytest
from admin_service.apps.game_config import config_text
from admin_service.apps.game_config.config_text import ConfigTextError

pytestmark = pytest.mark.unit

YAML = """\
key: music.daily.classic
domain: music
session:
  rounds: 5
  skip_allowed: true
scoring:
  type: by_level
  params:
    points: [6, 5, 4]
"""


def test_yaml_and_json_parse_to_the_same_body() -> None:
    body = config_text.parse(YAML, "yaml")
    assert body["session"] == {"rounds": 5, "skip_allowed": True}
    assert config_text.parse(config_text.dump(body, "json"), "json") == body


def test_dump_keeps_logical_top_level_order() -> None:
    body = {"session": {}, "domain": "music", "zzz": 1, "key": "music.a.b"}
    assert list(config_text.parse(config_text.dump(body, "yaml"), "yaml")) == [
        "key",
        "domain",
        "session",
        "zzz",
    ]


def test_yaml_dates_stay_strings() -> None:
    """jsonb дат не знает: YAML-дата не должна превращаться в объект date."""
    assert config_text.parse("key: x\nsince: 2026-10-10\n", "yaml")["since"] == "2026-10-10"


@pytest.mark.parametrize(
    ("text", "fmt", "line"),
    [
        ("a: 1\na: 2\n", "yaml", 2),
        ('{"a": 1, "a": 2}', "json", None),
        ("a: [1, 2\n", "yaml", 2),
        ('{"a": }', "json", 1),
    ],
)
def test_duplicates_and_syntax_errors_are_rejected(text: str, fmt: str, line: int | None) -> None:
    with pytest.raises(ConfigTextError) as exc:
        config_text.parse(text, fmt)  # type: ignore[arg-type]  # параметризация строкой
    assert exc.value.line == line


@pytest.mark.parametrize("text", ["", "   \n", "- 1\n- 2\n", "just a string", "1: a\n"])
def test_body_must_be_an_object_with_string_keys(text: str) -> None:
    with pytest.raises(ConfigTextError):
        config_text.parse(text, "yaml")


def test_diff_ignores_key_order_and_shows_changes() -> None:
    old = {"a": 1, "b": {"c": [1, 2]}}
    assert config_text.diff(old, {"b": {"c": [1, 2]}, "a": 1}, old_label="o", new_label="n") == []
    lines = config_text.diff(old, {"a": 2, "b": {"c": [1, 2]}}, old_label="v1", new_label="r3")
    kinds = {(line.kind, line.text) for line in lines}
    assert ("del", "-a: 1") in kinds
    assert ("add", "+a: 2") in kinds
    assert ("meta", "--- v1") in kinds


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("session.rounds", 4),
        ("session", 3),
        ("scoring.params.points[1]", 9),
        ("scoring.params.missing", 8),  # нет поля — ближайший существующий предок
        ("answer.matcher", 1),  # нет и предка — корень
        ("", 1),
        ("session..rounds", None),  # путь не разбирается
    ],
)
def test_line_of_maps_error_path_to_text_line(path: str, line: int | None) -> None:
    assert config_text.line_of(YAML, path) == line


def test_line_of_works_for_json_text_and_broken_text() -> None:
    text = config_text.dump(config_text.parse(YAML, "yaml"), "json")
    number = config_text.line_of(text, "session.rounds")
    assert number is not None
    assert '"rounds"' in text.splitlines()[number - 1]
    assert config_text.line_of("a: [", "a") is None


def test_numbered_marks_error_lines() -> None:
    rows = config_text.numbered("a\nb\nc", {2})
    assert [(r["number"], r["error"]) for r in rows] == [(1, False), (2, True), (3, False)]


def test_yaml_aliases_are_rejected() -> None:
    """«Billion laughs»: ссылки раскрылись бы экспоненциально при дампе в JSON."""
    bomb = 'a: &a ["x","x"]\nb: &b [*a,*a]\nc: [*b,*b]\n'
    with pytest.raises(ConfigTextError) as exc:
        config_text.parse(bomb, "yaml")
    assert exc.value.line == 2
    assert config_text.line_of(bomb, "c") is None


@pytest.mark.parametrize("fmt", ["json", "yaml"])
def test_too_deep_nesting_is_a_form_error(fmt: str) -> None:
    text = '{"a": ' + "[" * 20000 + "]" * 20000 + "}"
    with pytest.raises(ConfigTextError, match=r"вложенность|JSON|YAML"):
        config_text.parse(text, fmt)  # type: ignore[arg-type]  # параметризация строкой


ONLINE_YAML = """\
key: music.duel.classic
domain: music
session:
  kind: duel
  players: {min: 2, max: 2}
  rounds: 7
"""


@pytest.mark.parametrize(
    ("path", "line"),
    [
        ("session.players.max", 5),
        ("session.duel.players.max", 5),  # имя ветки union в пути пропускается
        ("session.duel", 3),  # за веткой ничего — ближайший предок
        ("session.duel.missing", 3),
    ],
)
def test_line_of_skips_union_branch_name(path: str, line: int) -> None:
    assert config_text.line_of(ONLINE_YAML, path) == line


def test_dump_orders_online_session_like_contract() -> None:
    body = {
        "key": "music.duel.classic",
        "session": {
            "reveal_s": 5,
            "chat": {"free_text": False, "emotes": []},
            "contest": {"params": {}, "type": "race"},
            "players": {"max": 2, "min": 2},
            "kind": "duel",
            "custom": 1,
        },
    }
    session = config_text.parse(config_text.dump(body, "yaml"), "yaml")["session"]
    assert list(session) == ["kind", "players", "reveal_s", "contest", "chat", "custom"]
    assert list(session["players"]) == ["min", "max"]
    assert list(session["contest"]) == ["type", "params"]
    assert list(session["chat"]) == ["emotes", "free_text"]
