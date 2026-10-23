"""Шаблоны рейтинговых дуэлей (4b): на каждую очередь `(ladder, format)` — ровно один шаблон.

match ищет шаблон по `session.ranked` + `session.ladder` + `answer.format` (ADR 0038,
`FORMAT_ANSWER`: classic → classic, lite → pick). Тест сверяет сиды двух сервисов файлами,
без межсервисных импортов.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from game_service.application.rules import RulesFactory

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
MODES = ROOT / "seeds" / "game" / "modes"
LADDERS = ROOT / "seeds" / "rating" / "ladders"
FORMAT_ANSWER = {"classic": "classic", "lite": "pick"}


def modes() -> list[dict[str, Any]]:
    return [yaml.safe_load(p.read_text("utf-8")) for p in sorted(MODES.glob("*.yaml"))]


def ladders() -> dict[str, dict[str, Any]]:
    return {p.stem: yaml.safe_load(p.read_text("utf-8")) for p in LADDERS.glob("*.yaml")}


def ranked_duels() -> list[dict[str, Any]]:
    return [m for m in modes() if m["session"].get("ranked")]


def test_every_queue_has_exactly_one_ranked_template() -> None:
    for key, ladder in ladders().items():
        for queue_format in ladder["formats"]:
            found = [
                m["key"]
                for m in ranked_duels()
                if m["session"]["ladder"] == key
                and m["session"]["kind"] == "duel"
                and m["answer"]["format"] == FORMAT_ANSWER[queue_format]
            ]
            assert len(found) == 1, (key, queue_format, found)


def test_ranked_templates_reference_known_ladders_of_same_domain() -> None:
    known = ladders()
    for mode in ranked_duels():
        ladder = mode["session"]["ladder"]
        assert ladder in known, mode["key"]
        assert known[ladder]["domain"] == mode["domain"], mode["key"]


def test_unranked_modes_have_no_ladder() -> None:
    assert all(m["session"].get("ladder") is None for m in modes() if m not in ranked_duels())


@pytest.mark.parametrize("domain", ["music", "film"])
def test_lite_templates_are_valid_and_use_distractors(plugin_registry: Any, domain: str) -> None:
    factory = RulesFactory(plugin_registry)
    lite = [m for m in modes() if m["domain"] == domain and m["key"].endswith(".lite")]

    assert {m["key"] for m in lite} == {f"{domain}.duel.lite", f"{domain}.ranked.lite"}
    for mode in lite:
        check = factory.check(mode)
        assert check.errors == [], mode["key"]
        assert check.rules is not None and check.rules.levels_count == 1
        distractors = mode["answer"]["params"]["distractors"]
        assert distractors["type"] == "similar"
        assert distractors["params"]["min_pool"] >= distractors["count"] + 1
