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


class TestDisplayTitle:
    """Проблема 40: версия не дописывается второй раз."""

    @pytest.mark.parametrize(
        ("title", "version", "expected"),
        [
            (
                "Hotel California (2013 Remaster)",
                "(2013 Remaster)",
                "Hotel California (2013 Remaster)",
            ),
            ("On The Floor (Radio Edit)", "(Radio Edit)", "On The Floor (Radio Edit)"),
            ("Song", "(Radio Edit)", "Song (Radio Edit)"),
            ("Song", "", "Song"),
            ("Song", "  ", "Song"),
        ],
    )
    def test_version_is_added_once(
        self, deezer: ModuleType, title: str, version: str, expected: str
    ) -> None:
        assert deezer.display_title(title, version) == expected


def _t(tid: int, artist: int, title: str, rank: int, album: int, *, version: str = "") -> dict:
    return {
        "id": tid,
        "artist_id": artist,
        "title": title,
        "version": version,
        "rank": rank,
        "album_id": album,
    }


def _albums(**years: int) -> dict[str, dict[str, str]]:
    return {a.removeprefix("a"): {"date": f"{y}-05-01"} for a, y in years.items()}


class TestEarliestYear:
    """Проблемы 7 и 51: год песни - самый ранний среди версий."""

    def test_takes_the_minimum_over_versions(self, deezer: ModuleType) -> None:
        albums = _albums(a1=2005, a2=2002, a3=2010)
        assert deezer.earliest_year([1, 2, 3], albums) == 2002

    def test_ignores_unknown_and_zero_dates(self, deezer: ModuleType) -> None:
        albums = {"1": {"date": "0000-00-00"}, "2": {"date": "1999-01-01"}}
        assert deezer.earliest_year([1, 2, 7], albums) == 1999
        assert deezer.earliest_year([1], albums) is None
        assert deezer.earliest_year([], albums) is None

    def test_dedupe_collects_albums_of_all_versions(self, deezer: ModuleType) -> None:
        visited = {
            "1": [
                _t(1, 1, "Lose Yourself", 10, album=100),
                _t(2, 1, "Lose Yourself (Remastered)", 99, album=200),
            ]
        }
        (song,) = deezer.dedupe_tracks(visited)
        assert song["id"] == 2  # лучшая по популярности
        assert song["album_ids"] == [100, 200]


class TestQuotas:
    def test_parse(self, deezer: ModuleType) -> None:
        assert deezer.parse_quotas("1980=600, 1995=700") == {1980: 600, 1990: 700}
        assert deezer.parse_quotas("") == {}
        with pytest.raises(ValueError, match="ГГГГ=число"):
            deezer.parse_quotas("80s")

    def _cands(self) -> tuple[list[dict], dict]:
        # популярные 2010-е и менее популярные 80-е
        modern = [_t(n, n, f"m{n}", 1000 - n, album=n) for n in range(10)]
        old = [_t(100 + n, 100 + n, f"o{n}", 100 - n, album=100 + n) for n in range(5)]
        albums = {str(t["album_id"]): {"date": "2012-01-01"} for t in modern}
        albums |= {str(t["album_id"]): {"date": "1985-01-01"} for t in old}
        cands = [{**t, "album_ids": [t["album_id"]]} for t in modern + old]
        return cands, albums

    def test_quota_pulls_old_tracks_above_popular_modern_ones(self, deezer: ModuleType) -> None:
        cands, albums = self._cands()
        picked = deezer.pick_with_quotas(cands, albums, count=8, per_artist=40, quotas={1980: 3})
        years = [deezer.earliest_year(t["album_ids"], albums) for t in picked]
        assert len(picked) == 8
        assert years.count(1985) == 3
        assert [t["rank"] for t in picked] == sorted((t["rank"] for t in picked), reverse=True)

    def test_unreachable_quota_takes_what_exists(self, deezer: ModuleType) -> None:
        cands, albums = self._cands()
        picked = deezer.pick_with_quotas(cands, albums, count=8, per_artist=40, quotas={1980: 50})
        assert deezer.decade_counts(picked, albums)[1980] == 5
        assert len(picked) == 8
        assert deezer.quota_shortfalls(picked, albums, {1980: 50}) == {1980: 45}

    def test_without_quotas_it_is_top_by_rank_with_artist_limit(self, deezer: ModuleType) -> None:
        cands = [{**_t(n, 1, f"s{n}", 100 - n, album=n), "album_ids": [n]} for n in range(10)]
        picked = deezer.pick_with_quotas(cands, {}, count=5, per_artist=3, quotas={})
        assert [t["id"] for t in picked] == [0, 1, 2]

    def test_table_marks_shortfall(self, deezer: ModuleType) -> None:
        cands, albums = self._cands()
        picked = deezer.pick_with_quotas(cands, albums, count=8, per_artist=40, quotas={1980: 50})
        table = deezer.quota_table(picked, albums, {1980: 50, 1990: 10})
        assert "  1980-е: 5 / 50 (МЕНЬШЕ КВОТЫ)" in table
        assert "  1990-е: 0 / 10 (МЕНЬШЕ КВОТЫ)" in table
        assert "  2010-е: 3 / -" in table


class TestArtistPhoto:
    def test_placeholder_is_not_a_photo(self, deezer: ModuleType) -> None:
        assert deezer.has_artist_photo("https://cdn-images.dzcdn.net/images/artist/abc/500x500.jpg")
        assert not deezer.has_artist_photo(
            "https://cdn-images.dzcdn.net/images/artist//500x500.jpg"
        )
        assert not deezer.has_artist_photo("")
