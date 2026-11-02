"""Тесты чистых функций выгрузки TMDB (``seeds/media/fetch_tmdb_popular.py``): без сети."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[3] / "seeds" / "media" / "fetch_tmdb_popular.py"


@pytest.fixture(scope="module")
def tmdb() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_tmdb_popular", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _b(path: str, lang: str | None, vote: float) -> dict[str, Any]:
    return {"file_path": path, "iso_639_1": lang, "vote_average": vote}


def _clean(n: int) -> list[dict[str, Any]]:
    return [_b(f"/c{i}.jpg", None, 6.0 - i * 0.1) for i in range(n)]


def _always(_backdrop: dict[str, Any], _position: int) -> bool:
    return True


def _paths(frames: list[dict[str, Any]] | None) -> list[str]:
    assert frames is not None
    return [b["file_path"] for b in frames]


class TestPickFrames:
    """Проблема 54: вместо кадра не должна попадать афиша с надписью."""

    def test_clean_frames_go_first_even_if_language_ones_score_higher(
        self, tmdb: ModuleType
    ) -> None:
        backdrops = [_b("/ru.jpg", "ru", 9.9), *_clean(6)]
        assert _paths(tmdb.pick_frames(backdrops, _always)) == [f"/c{i}.jpg" for i in range(6)]

    def test_shortage_is_filled_only_with_high_rated_language_frames(
        self, tmdb: ModuleType
    ) -> None:
        backdrops = [*_clean(5), _b("/en-low.jpg", "en", 4.9), _b("/en-high.jpg", "en", 5.0)]
        paths = _paths(tmdb.pick_frames(backdrops, _always))
        assert paths[-1] == "/en-high.jpg"
        assert "/en-low.jpg" not in paths

    def test_at_most_two_language_frames(self, tmdb: ModuleType) -> None:
        langs = [_b(f"/l{i}.jpg", "en", 8.0) for i in range(5)]
        picked = tmdb.pick_frames([*_clean(4), *langs], _always)
        assert len(picked) == 6
        assert sum(b["iso_639_1"] is not None for b in picked) == 2

    def test_title_is_dropped_when_six_not_reached(self, tmdb: ModuleType) -> None:
        langs = [_b(f"/l{i}.jpg", "en", 8.0) for i in range(5)]
        assert tmdb.pick_frames([*_clean(3), *langs], _always) is None  # 3 + не больше 2 = 5

    def test_min_vote_is_configurable(self, tmdb: ModuleType) -> None:
        backdrops = [*_clean(5), _b("/en.jpg", "en", 3.0)]
        assert tmdb.pick_frames(backdrops, _always) is None
        assert len(tmdb.pick_frames(backdrops, _always, min_vote=3.0)) == 6

    def test_rejected_frames_are_skipped_and_positions_stay_dense(self, tmdb: ModuleType) -> None:
        positions: list[tuple[str, int]] = []

        def accept(backdrop: dict[str, Any], position: int) -> bool:
            positions.append((backdrop["file_path"], position))
            return backdrop["file_path"] != "/c1.jpg"  # повтор афиши

        picked = tmdb.pick_frames(_clean(8), accept)
        assert _paths(picked) == [f"/c{i}.jpg" for i in (0, 2, 3, 4, 5, 6)]
        assert ("/c1.jpg", 2) in positions
        assert ("/c2.jpg", 2) in positions  # место отброшенного занимает следующий


class TestPosterHash:
    def test_same_picture_repeats_poster_and_different_does_not(
        self, tmdb: ModuleType, tmp_path: Path
    ) -> None:
        image = pytest.importorskip("PIL.Image")
        poster, same, other = (tmp_path / n for n in ("poster.jpg", "same.jpg", "other.jpg"))
        gradient = image.linear_gradient("L").rotate(90, expand=True).resize((200, 300))
        gradient.save(poster)
        gradient.resize((400, 225)).save(same)
        gradient.transpose(image.Transpose.FLIP_LEFT_RIGHT).save(other)
        assert tmdb.repeats_poster(same, poster)
        assert not tmdb.repeats_poster(other, poster)

    def test_unreadable_file_is_not_a_repeat(self, tmdb: ModuleType, tmp_path: Path) -> None:
        broken = tmp_path / "x.jpg"
        broken.write_bytes(b"not an image")
        assert not tmdb.repeats_poster(broken, broken)


class TestPeople:
    def test_movie_directors_and_series_creators(self, tmdb: ModuleType) -> None:
        movie = {"credits": {"crew": [{"job": "Writer", "id": 1}, {"job": "Director", "id": 2}]}}
        people, role = tmdb.credits_people(movie, "movie")
        assert [p["id"] for p in people] == [2]
        assert role == "director"
        people, role = tmdb.credits_people({"created_by": [{"id": 5}, {"id": 6}, {"id": 7}]}, "tv")
        assert [p["id"] for p in people] == [5, 6]
        assert role == "creator"

    def test_photo_of_first_person_that_has_one(
        self, tmdb: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        urls: list[str] = []

        def fake_download(url: str, dest: Path) -> bool:
            urls.append(url)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return True

        monkeypatch.setattr(tmdb, "download", fake_download)
        people = [{"id": 1, "profile_path": None}, {"id": 2, "profile_path": "/p.jpg"}]
        assert tmdb.person_photo(people, tmp_path) == "persons/2.jpg"
        assert urls == [f"{tmdb.IMG}/w342/p.jpg"]
        assert (tmp_path / "persons" / "2.jpg").is_file()
        assert tmdb.person_photo([{"id": 3, "profile_path": None}], tmp_path) is None
