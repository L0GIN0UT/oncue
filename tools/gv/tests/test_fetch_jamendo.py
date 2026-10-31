"""Тесты чистых функций выгрузки Jamendo (``seeds/media/fetch_jamendo.py``): без сети."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "seeds" / "media" / "fetch_jamendo.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_jamendo", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fj = _load()

RAW: dict[str, Any] = json.loads(
    """{
  "id": "1214935", "name": "Wish You Were Here", "duration": 270,
  "artist_name": "The.madpix.project", "album_name": "Wish You Were Here", "album_id": "145774",
  "license_ccurl": "http://creativecommons.org/licenses/by-nc-sa/3.0/",
  "releasedate": "2015-03-29", "shareurl": "https://www.jamendo.com/track/1214935",
  "album_image": "https://usercontent.jamendo.com?type=album&id=145774&width=600",
  "audiodownload": "https://prod-1.storage.jamendo.com/download/track/1214935/mp32/",
  "audiodownload_allowed": true,
  "musicinfo": {"tags": {"genres": ["Dance", "electronic", "house"]}}
}"""
)


def test_parse_track_maps_fields() -> None:
    track = fj.parse_track(RAW, "electronic")
    assert track is not None
    assert track["id"] == 1214935
    assert track["year"] == 2015
    assert track["album_id"] == 145774
    # запрошенный жанр первым, без дублей, не больше трёх
    assert track["genres"] == ["electronic", "dance", "house"]
    assert track["license"].endswith("by-nc-sa/3.0/")


@pytest.mark.parametrize(
    "patch",
    [
        {"audiodownload_allowed": False},
        {"audiodownload": ""},
        {"name": ""},
        {"name": "x" * 60},
        {"artist_name": " "},
        {"album_image": "", "image": ""},
        {"album_id": ""},
        {"duration": 10},
    ],
)
def test_parse_track_rejects_unfit(patch: dict[str, Any]) -> None:
    assert fj.parse_track({**RAW, **patch}, "rock") is None


def test_parse_track_without_year() -> None:
    track = fj.parse_track({**RAW, "releasedate": ""}, "rock")
    assert track is not None
    assert track["year"] is None


@pytest.mark.parametrize(("duration", "start"), [(270, 30), (60, 30), (59, 29), (40, 10), (30, 0)])
def test_clip_start(duration: int, start: int) -> None:
    assert fj.clip_start(duration) == start


def test_clip_command_cuts_thirty_seconds() -> None:
    cmd = fj.clip_command("ffmpeg", Path("a.mp3"), Path("b.mp3"), 30)
    assert cmd[cmd.index("-ss") + 1] == "30"
    assert cmd[cmd.index("-t") + 1] == "30"
    assert cmd[cmd.index("-i") + 1] == "a.mp3"
    assert "-vn" in cmd
    assert cmd[-1] == "b.mp3"


def test_catalog_row_has_import_fields() -> None:
    track = fj.parse_track(RAW, "electronic")
    row = fj.catalog_row(track)
    assert row["key"] == "track:jamendo-1214935"
    assert row["kind"] == "track"
    assert row["persons"] == [{"name": "The.madpix.project", "role": "artist"}]
    assert row["metrics"] == {"duration_ms": 30000}
    assert row["origin"] == "jamendo"
    assert row["audio"] == "1214935.mp3"
    assert row["cover"] == "145774.jpg"
    assert row["license"] == "http://creativecommons.org/licenses/by-nc-sa/3.0/"
    assert row["source_url"] == "https://www.jamendo.com/track/1214935"
    json.dumps(row)  # сериализуется


def test_is_jpeg() -> None:
    assert fj.is_jpeg(b"\xff\xd8\xff\xe0rest")
    assert not fj.is_jpeg(b"\x89PNG\r\n")
    assert not fj.is_jpeg(b"")
