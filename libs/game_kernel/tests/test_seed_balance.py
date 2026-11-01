"""Баланс сидов режимов (пункты 19, 30, 51): окна уровней онлайна и пресеты случайных режимов."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
MODES = ROOT / "seeds/game/modes"
MESSAGES = ROOT / "frontend/messages/ru.json"


def _seed(key: str) -> dict[str, Any]:
    body: dict[str, Any] = yaml.safe_load((MODES / f"{key}.yaml").read_text(encoding="utf-8"))
    return body


@pytest.mark.parametrize("key", ["film.duel.frame", "music.duel.classic"])
def test_duel_windows_do_not_shrink_and_grow_late(key: str) -> None:
    """Пункт 19: окна дуэли не убывают, а на последнем уровне больше, чем на первом."""
    windows = _seed(key)["session"]["level_windows_s"]
    assert windows == sorted(windows)
    assert windows[-1] > windows[0]
    assert len(windows) == len(_seed(key)["clue"]["params"]["levels"])


def test_duel_windows_values_from_owner_decision() -> None:
    assert _seed("film.duel.frame")["session"]["level_windows_s"] == [15, 15, 20, 25, 30, 35]
    assert _seed("music.duel.classic")["session"]["level_windows_s"] == [25, 40]


def test_music_lobby_windows_are_longer_and_default_is_middle() -> None:
    """Пункт 30: музыкальное лобби - окна на ~50% больше, по умолчанию среднее."""
    session = _seed("music.lobby.classic")["session"]
    variants = session["lobby"]["overridable"]["level_window_s"]
    assert variants == [20, 35, 55]
    assert session["level_windows_s"] == [variants[1]]


def test_film_online_windows_unchanged() -> None:
    assert _seed("film.party.frame")["session"]["level_windows_s"] == [20] * 6
    assert _seed("film.lobby.collection")["session"]["level_windows_s"] == [25] * 6
    lobby = _seed("film.lobby.collection")["session"]["lobby"]["overridable"]
    assert lobby["level_window_s"] == [15, 25, 40]


@pytest.mark.parametrize("key", ["music.random.classic", "film.random.frame"])
def test_preset_ids_unique_and_labels_exist(key: str) -> None:
    """Пункт 51: подписи пресетов вынесены в i18n ``modes.presets.*`` и есть в ru.json."""
    presets = _seed(key)["source"]["params"]["presets"]
    ids = [p["id"] for p in presets]
    assert len(ids) == len(set(ids))
    labels = json.loads(MESSAGES.read_text(encoding="utf-8"))
    names = _find_presets(labels)
    for preset in presets:
        prefix, _, name = preset["title_i18n"].rpartition(".")
        assert prefix == "modes.presets"
        assert name in names, f"нет подписи {preset['title_i18n']}"


def _find_presets(node: Any) -> dict[str, str]:
    """Блок ``modes.presets`` из дерева сообщений (ключ ``presets`` внутри ``modes``)."""
    found = node["modes"]["presets"]
    assert isinstance(found, dict)
    return found


def test_music_presets_cover_decades_without_overlap() -> None:
    presets = _seed("music.random.classic")["source"]["params"]["presets"]
    ranges = sorted(
        (p["filters"]["years"].get("from", 0), p["filters"]["years"].get("to", 9999))
        for p in presets
        if "years" in p["filters"]
    )
    assert len(ranges) >= 6
    for (_, prev_to), (next_from, _) in pairwise(ranges):
        assert prev_to < next_from
    assert not any(p["id"] in {"oldies", "modern"} for p in presets)


def test_music_genre_presets_use_catalog_genres() -> None:
    presets = {p["id"]: p for p in _seed("music.random.classic")["source"]["params"]["presets"]}
    assert presets["hip_hop"]["filters"]["genres_any"] == ["hip-hop"]
    assert presets["rnb"]["filters"]["genres_any"] == ["rnb"]
    for key in ("pop", "rock", "alternative", "electronic"):
        assert presets[key]["filters"]["genres_any"]
