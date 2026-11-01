"""Тесты чистых функций выгрузки Deezer (``seeds/media/fetch_deezer.py``): без сети и без диска."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[3] / "seeds" / "media" / "fetch_deezer.py"


@pytest.fixture(scope="module")
def deezer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_deezer", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestNormalizedTitle:
    def test_remaster_and_feat_noise_is_dropped(self, deezer: ModuleType) -> None:
        a = deezer.normalized_title("Song (2011 Remaster)", "")
        b = deezer.normalized_title("Song (feat. Someone)", "")
        assert a == b == deezer.normalized_title("Song", "")

    def test_remix_and_live_stay_separate_tracks(self, deezer: ModuleType) -> None:
        plain = deezer.normalized_title("Song", "")
        assert deezer.normalized_title("Song (Club Remix)", "") != plain
        assert deezer.normalized_title("Song - Live", "") != plain


class TestGenreKey:
    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            ([], "pop"),
            (["Rap/Hip Hop"], "hip-hop"),
            (["R&B"], "rnb"),
            (["Films/Games"], "soundtrack"),
            (["Latin Music"], "latin"),
            (["Reggae"], "reggae"),
            (["Alternative"], "alternative"),
        ],
    )
    def test_maps_to_seed_real_genres(
        self, deezer: ModuleType, names: list[str], expected: str
    ) -> None:
        assert deezer.genre_key(names) == expected

    def test_every_genre_has_a_collection_title(self, deezer: ModuleType) -> None:
        from gv_cli.seed_real import GENRE_TITLES

        for key in set(deezer.GENRE_NAMES.values()):
            assert key in GENRE_TITLES, key


class TestSelectTracks:
    @staticmethod
    def _track(tid: int, artist: int, title: str, rank: int) -> dict[str, object]:
        return {"id": tid, "artist_id": artist, "title": title, "version": "", "rank": rank}

    def test_duplicates_are_merged_by_best_rank_and_karaoke_dropped(
        self, deezer: ModuleType
    ) -> None:
        visited = {
            "1": [
                self._track(1, 1, "Song", 10),
                self._track(2, 1, "Song (Remastered)", 99),
                self._track(3, 1, "Song - Karaoke Version", 500),
            ]
        }
        (chosen,) = deezer.select_tracks(visited, count=10, per_artist=40)
        assert chosen["id"] == 2

    def test_per_artist_limit_and_count(self, deezer: ModuleType) -> None:
        visited = {"1": [self._track(n, 1, f"s{n}", n) for n in range(10)]}
        assert len(deezer.select_tracks(visited, count=100, per_artist=3)) == 3
        assert len(deezer.select_tracks(visited, count=2, per_artist=40)) == 2
