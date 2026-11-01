"""Тесты чистых функций ``gv dev seed-real``: разбор каталога, сборники, подготовка аудио."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from gv_cli import seed_real
from gv_cli.seed import CollectionItemSpec, CollectionSpec, ModePlan, SeedConfig, SeedError
from gv_cli.seed_real import (
    GENRE_TITLES,
    TARGET_AUDIO_S,
    PackageEntry,
    PackageFile,
    RealSeeder,
    RealUnit,
    SkippedMode,
    audio_target,
    build_collections,
    build_units,
    catalog_row,
    ffmpeg_command,
    film_collections,
    import_rows,
    load_film_rows,
    load_track_rows,
    music_collections,
    normalize_genre,
    package_manifest,
    prepare_audio,
    read_jsonl,
    report_broken,
    slugify,
    split_batches,
    unit_files,
    unit_provenance,
    with_featured,
    write_jsonl,
    write_package,
)

pytestmark = pytest.mark.unit


def _track(n: int, genre: str, listens: int = 0) -> RealUnit:
    return RealUnit(
        key=f"track:demo-{n}",
        domain="music",
        title=f"t{n}",
        year=2010,
        genres=(genre,),
        popularity=listens,
        audio=Path(f"{n:06d}.mp3"),
    )


def _film(
    n: int,
    year: int,
    rating: float,
    genres: tuple[str, ...] = (),
    *,
    category: str = "Фильмы",
    popularity: float = 0.0,
) -> RealUnit:
    return RealUnit(
        key=f"film:tmdb-{n}",
        domain="film",
        title=f"f{n}",
        year=year,
        genres=genres,
        rating=rating,
        popularity=popularity,
        category=category,
        media_type="movie",
    )


class TestCatalogRow:
    def test_film_overview_goes_to_clue_texts_and_extras_dropped(self) -> None:
        raw = {
            "key": "film:tmdb-1",
            "kind": "film",
            "title": "Фильм",
            "year": 1999,
            "metrics": {"vote_average": 8.1, "runtime_min": 100},
            "overview": "Сюжет.",
            "poster": "poster.jpg",
            "frames": ["frame-1.jpg"],
        }
        row = catalog_row(raw)
        assert row["clue_texts"] == {"plot_raw": "Сюжет."}
        assert not {"overview", "poster", "frames"} & set(row)

    def test_track_listens_become_popularity_and_html_is_unescaped(self) -> None:
        raw = {
            "key": "track:demo-2",
            "kind": "track",
            "title": "A &amp; B",
            "year": None,
            "persons": [{"name": "Tom &amp; Jerry", "role": "artist"}],
            "metrics": {"duration_ms": 30000, "listens": 7},
            "album": "x",
            "license": "CC",
            "audio": "000002.mp3",
        }
        row = catalog_row(raw)
        assert row["title"] == "A & B"
        assert row["persons"][0]["name"] == "Tom & Jerry"
        assert row["metrics"] == {"duration_ms": 30000, "popularity": 7}
        assert "year" not in row
        assert not {"album", "license", "audio"} & set(row)

    def test_series_creator_becomes_director_and_ref_is_prefixed(self) -> None:
        raw = {
            "key": "film:tmdb-tv-1399",
            "kind": "film",
            "title": "Сериал",
            "category": "Сериалы",
            "media_type": "tv",
            "persons": [{"name": "Автор", "role": "creator"}, {"name": "Икс", "role": "writer"}],
            "metrics": {"vote_average": 8.4, "popularity": 120.5, "seasons": 8},
            "external_refs": {"tmdb": "1399", "tmdb_type": "tv"},
            "folder": "Сериалы/tv-1399",
        }
        row = catalog_row(raw)
        assert row["persons"] == [{"name": "Автор", "role": "director"}]
        assert row["external_refs"] == {"tmdb": "tv-1399"}  # не склеится с фильмом 1399
        assert row["metrics"] == {"vote_average": 8.4, "popularity": 120.5}
        assert not {"category", "media_type", "folder"} & set(row)

    def test_movie_ref_is_kept_as_is(self) -> None:
        raw = {
            "key": "film:tmdb-550",
            "kind": "film",
            "title": "Фильм",
            "media_type": "movie",
            "persons": [{"name": "Режиссёр", "role": "director"}],
            "external_refs": {"tmdb": "550", "tmdb_type": "movie"},
        }
        row = catalog_row(raw)
        assert row["external_refs"] == {"tmdb": "550"}
        assert row["persons"][0]["role"] == "director"

    def test_row_without_persons_has_no_persons_key(self) -> None:
        raw = {"key": "film:x", "kind": "film", "title": "x", "persons": []}
        assert "persons" not in catalog_row(raw)

    def test_row_without_overview_has_no_clue_texts(self) -> None:
        assert "clue_texts" not in catalog_row({"key": "film:x", "kind": "film", "title": "x"})


class TestReadJsonl:
    def test_skips_blank_and_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "a.jsonl"
        path.write_text('# c\n\n{"key": "a"}\n', encoding="utf-8")
        assert read_jsonl(path) == [{"key": "a"}]

    def test_row_without_key_is_error(self, tmp_path: Path) -> None:
        path = tmp_path / "a.jsonl"
        path.write_text('{"title": "a"}\n', encoding="utf-8")
        with pytest.raises(SeedError):
            read_jsonl(path)


class TestCollections:
    def test_music_top_and_genre_collections_sorted_by_popularity(self) -> None:
        units = [_track(i, "pop", listens=i) for i in range(60)]
        units += [_track(100 + i, "rock", listens=1000 + i) for i in range(25)]
        units += [_track(200 + i, "jazz") for i in range(5)]  # мало: без своего сборника
        specs = {s.key: s for s in music_collections(units, min_size=20, max_size=50)}
        assert list(specs) == ["real-music-top", "real-music-pop", "real-music-rock"]
        assert specs["real-music-top"].title == "Топ треков"
        top = [i.seed_key for i in specs["real-music-top"].items]
        assert len(top) == 50 and top[0] == "track:demo-124"  # самый популярный - из рока
        assert specs["real-music-pop"].title == "Поп"
        pop = [i.seed_key for i in specs["real-music-pop"].items]
        assert len(pop) == 50 and pop[0] == "track:demo-59"
        assert specs["real-music-rock"].default_mode_key == "music.collection.intro"

    def test_every_big_genre_gets_a_collection_and_unknown_title(self) -> None:
        units = [_track(g * 100 + i, f"g{g}") for g in range(8) for i in range(21)]
        specs = music_collections(units, min_size=20)
        assert len(specs) == 9  # топ + 8 жанров (раньше было не больше 6)
        assert len({s.key for s in specs}) == 9
        assert specs[1].title.startswith("Жанр: ")

    def test_collection_size_is_a_parameter(self) -> None:
        units = [_track(i, "pop", listens=i) for i in range(80)]
        sizes = {len(s.items) for s in music_collections(units, min_size=10, max_size=15)}
        assert sizes == {15}

    @pytest.mark.parametrize(
        "genre",
        [
            "pop", "hip-hop", "rock", "rnb", "dance", "electronic", "alternative", "soundtrack",
            "latin", "soul", "world", "metal", "jazz", "reggae", "classical", "folk", "blues",
        ],
    )  # fmt: skip
    def test_deezer_genres_have_titles(self, genre: str) -> None:
        assert genre in GENRE_TITLES

    def test_film_collections_top_categories_genres_decades(self) -> None:
        units = [
            _film(n, 1995, 7.0, ("драма", "комедия"), popularity=float(n)) for n in range(1, 31)
        ]
        units += [
            _film(100 + n, 2005, 8.0, ("драма",), category="Аниме", popularity=float(n))
            for n in range(25)
        ]
        specs = {s.key: s for s in film_collections(units, min_size=20, max_size=10)}
        assert set(specs) == {
            "real-film-top",
            "real-film-filmy-top",
            "real-film-filmy-drama",
            "real-film-filmy-komediya",
            "real-film-anime-top",
            "real-film-anime-drama",
            "real-film-1990s",
            "real-film-2000s",
        }
        top = [i.seed_key for i in specs["real-film-top"].items]
        assert len(top) == 10 and top[0] == "film:tmdb-30"  # popularity 30 у «Фильмов»
        assert specs["real-film-anime-top"].title == "Аниме: топ"
        assert specs["real-film-anime-drama"].title == "Аниме: драма"
        assert all(s.default_mode_key == "film.collection.frame" for s in specs.values())
        assert all(s.key.isascii() for s in specs.values())

    def test_film_ties_are_broken_by_rating(self) -> None:
        units = [_film(n, 1990, float(n % 10), popularity=1.0) for n in range(1, 25)]
        (top, *_rest) = film_collections(units, min_size=20, max_size=3)
        assert next(i.seed_key for i in top.items) in {"film:tmdb-9", "film:tmdb-19"}

    def test_small_collections_are_skipped(self) -> None:
        units = [_film(i, 1985, 5.0) for i in range(3)]
        assert film_collections(units) == []
        assert music_collections([_track(1, "pop")]) == []

    def test_build_collections_joins_music_and_film(self) -> None:
        units = [_track(i, "pop") for i in range(25)] + [_film(i, 1990, 5.0) for i in range(25)]
        domains = {s.domain for s in build_collections(units)}
        assert domains == {"music", "film"}

    def test_slugify_transliterates(self) -> None:
        assert slugify("Мультфильмы") == "multfilmy"
        assert slugify("научная фантастика") == "nauchnaya-fantastika"
        assert slugify("hip-hop") == "hip-hop"
        assert slugify("???") == ""


class TestAudio:
    def test_ffmpeg_command_loops_to_target_length(self) -> None:
        cmd = ffmpeg_command("ffmpeg", Path("in.mp3"), Path("out.m4a"))
        assert cmd[cmd.index("-stream_loop") + 1] == "-1"
        assert cmd[cmd.index("-t") + 1] == str(TARGET_AUDIO_S)
        assert cmd[-1] == "out.m4a"

    @staticmethod
    def _writing_runner(calls: list[list[str]], fail_on: str | None = None) -> Any:
        def fake(cmd: Any) -> None:
            calls.append(list(cmd))
            if fail_on is not None and fail_on in str(cmd[cmd.index("-i") + 1]):
                raise subprocess.CalledProcessError(1, cmd)
            Path(cmd[-1]).write_bytes(b"m4a")

        return fake

    def test_prepare_audio_skips_ready_files_and_encodes_via_part_file(
        self, tmp_path: Path
    ) -> None:
        units = [_track(1, "folk"), _track(2, "folk")]
        ready = audio_target(tmp_path, units[0].key)
        ready.parent.mkdir(parents=True)
        ready.write_bytes(b"m4a")
        calls: list[list[str]] = []

        result, failed = prepare_audio(units, tmp_path, "ffmpeg", self._writing_runner(calls))

        assert failed == {}
        assert set(result) == {u.key for u in units}
        assert len(calls) == 1
        assert calls[0][-1].endswith(".part.m4a")  # кэш пополняется только готовым файлом
        assert audio_target(tmp_path, units[1].key).read_bytes() == b"m4a"
        assert not list(tmp_path.rglob("*.part.m4a"))

    def test_prepare_audio_runs_in_parallel_threads(self, tmp_path: Path) -> None:
        import threading

        units = [_track(n, "folk") for n in range(12)]
        barrier = threading.Barrier(3, timeout=5)
        started = {"n": 0}
        lock = threading.Lock()

        def fake(cmd: Any) -> None:
            with lock:
                started["n"] += 1
                first = started["n"] <= 3
            if first:
                barrier.wait()  # три клипа кодируются одновременно, иначе барьер не откроется
            Path(cmd[-1]).write_bytes(b"m4a")

        result, failed = prepare_audio(units, tmp_path, "ffmpeg", fake, jobs=3)
        assert len(result) == 12 and failed == {}

    def test_broken_clip_is_reported_not_raised(self, tmp_path: Path) -> None:
        units = [_track(1, "folk"), _track(2, "folk")]
        calls: list[list[str]] = []
        lines: list[str] = []
        result, failed = prepare_audio(
            units,
            tmp_path,
            "ffmpeg",
            self._writing_runner(calls, fail_on="000002"),
            echo=lines.append,
        )
        assert set(result) == {units[0].key}
        assert set(failed) == {units[1].key}
        assert not audio_target(tmp_path, units[1].key).exists()
        assert not list(tmp_path.rglob("*.part.m4a"))
        assert any("аудио 2/2" in line for line in lines)

    def test_runner_that_creates_nothing_counts_as_failure(self, tmp_path: Path) -> None:
        result, failed = prepare_audio([_track(1, "folk")], tmp_path, "ffmpeg", lambda _c: None)
        assert result == {} and "track:demo-1" in failed


class TestMissingSummary:
    def test_shows_total_and_first_keys(self) -> None:
        from gv_cli.seed import _missing_summary

        text = _missing_summary({f"k{n:02d}" for n in range(30)}, shown=3)
        assert "(30)" in text
        assert "k00" in text
        assert "k03" not in text


# ---------------------------------------------------------------- клиент пакета media-pipeline
class TestPackage:
    def _entries(self, tmp_path: Path) -> list[PackageEntry]:
        audio = tmp_path / "a.m4a"
        audio.write_bytes(b"audio")
        poster = tmp_path / "p.jpg"
        poster.write_bytes(b"poster")
        frames = tuple(tmp_path / f"f{n}.jpg" for n in range(1, 4))
        for f in frames:
            f.write_bytes(f.name.encode())
        track = _track(1, "folk")
        film = RealUnit(
            key="film:tmdb-550",
            domain="film",
            title="f",
            year=1999,
            genres=(),
            poster=poster,
            frames=frames,
        )
        return [
            PackageEntry(track, "0192a3b4-c5d6-7e8f-9a0b-000000000101", unit_files(track, audio)),
            PackageEntry(film, "0192a3b4-c5d6-7e8f-9a0b-000000000102", unit_files(film, None)),
        ]

    def test_manifest_matches_contract_schema(self, tmp_path: Path) -> None:
        from jsonschema import Draft202012Validator

        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/packages/media_package_manifest.v1.json"
            ).read_text(encoding="utf-8")
        )
        manifest = package_manifest(self._entries(tmp_path))
        assert not list(Draft202012Validator(schema).iter_errors(manifest))
        track, film = manifest["items"]
        assert (track["kind"], track["origin"]) == ("track", "upload")
        assert (film["kind"], film["origin"]) == ("film", "tmdb")
        assert film["source_url"] == "https://www.themoviedb.org/movie/550"
        assert [(f["role"], f.get("position")) for f in film["files"]] == [
            ("frame", 1),
            ("frame", 2),
            ("frame", 3),
            ("poster", None),
        ]

    def test_manifest_accepts_cover_role(self, tmp_path: Path) -> None:
        """Роль ``cover`` (формат манифеста 1.1, ADR 0073) проходит схему контракта."""
        from jsonschema import Draft202012Validator

        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/packages/media_package_manifest.v1.json"
            ).read_text(encoding="utf-8")
        )
        cover = tmp_path / "cover.jpg"
        cover.write_bytes(b"c.jpg")
        track, _ = self._entries(tmp_path)
        entry = PackageEntry(
            track.unit, track.media_item_id, (*track.files, PackageFile("cover", cover))
        )
        manifest = package_manifest([entry])
        assert not list(Draft202012Validator(schema).iter_errors(manifest))
        assert [f["role"] for f in manifest["items"][0]["files"]] == ["audio", "cover"]
        assert manifest["items"][0]["files"][1]["path"].endswith("/cover.jpg")

    def test_write_package_stores_files_without_compression(self, tmp_path: Path) -> None:
        import zipfile

        entries = self._entries(tmp_path)
        archive = tmp_path / "p.zip"
        assert write_package(archive, entries) == 5
        with zipfile.ZipFile(archive) as zf:
            assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}
            manifest = json.loads(zf.read("manifest.json"))
            for item in manifest["items"]:
                for file in item["files"]:
                    assert zf.getinfo(file["path"]).file_size > 0
            assert zf.read("film-tmdb-550/frame-2.jpg") == b"f2.jpg"

    def test_split_batches(self, tmp_path: Path) -> None:
        entries = self._entries(tmp_path) * 3
        assert [len(b) for b in split_batches(entries, 4)] == [4, 2]

    def test_wait_package_reports_errors_and_returns_failed_keys(self) -> None:
        status = {
            "status": "partial",
            "total": 3,
            "done": 2,
            "failed": 1,
            "missing_source": 2,
            "missing_license": 2,
            "errors": [{"item_key": "film:tmdb-1", "path": "x.jpg", "code": "source.unsupported"}],
        }
        seeder = _bare_seeder(status)
        assert seeder._wait_package("pkg") == {"film:tmdb-1"}
        assert any("film:tmdb-1" in line for line in seeder.lines)

    def test_wait_package_raises_when_package_is_rejected(self) -> None:
        seeder = _bare_seeder(
            {"status": "failed", "done": 0, "total": 0, "error_code": "package.symlink",
             "error_message": "в архиве ссылки", "errors": []}
        )  # fmt: skip
        with pytest.raises(SeedError, match=r"package.symlink"):
            seeder._wait_package("pkg")


def _bare_seeder(status: dict[str, object]) -> Any:
    """RealSeeder без сети: только опрос статуса пакета."""

    class Resp:
        status_code = 200

        def json(self) -> dict[str, object]:
            return status

    class Media:
        def get(self, _url: str) -> Resp:
            return Resp()

    seeder = RealSeeder.__new__(RealSeeder)
    seeder.lines = []  # type: ignore[attr-defined]
    seeder._media = Media()  # type: ignore[assignment]
    seeder._package_timeout_s = 1.0
    seeder._echo = seeder.lines.append  # type: ignore[attr-defined]
    seeder._keep_token = lambda: None  # type: ignore[method-assign]
    seeder._check = lambda *_a, **_k: None  # type: ignore[method-assign]
    seeder._poll = lambda check, **_k: check()  # type: ignore[method-assign]
    return seeder


# ---------------------------------------------------------------- Deezer и обложки (ADR 0073)
def _deezer_row(track_id: int, genre: str = "rock", *, album_id: int = 7) -> dict[str, Any]:
    """Строка ``deezer/catalog-tracks.jsonl`` (формат ``seeds/media/fetch_deezer.py``)."""
    return {
        "key": f"track:deezer-{track_id}",
        "kind": "track",
        "title": f"d{track_id}",
        "year": 2015,
        "persons": [{"name": "Artist", "role": "artist"}],
        "genres": [genre],
        "metrics": {"duration_ms": 30000, "listens": 900000 + track_id},
        "album": "Album",
        "license": "",
        "source_url": f"https://www.deezer.com/track/{track_id}",
        "origin": "deezer",
        "audio": f"{track_id}.mp3",
        "cover": f"{album_id}.jpg",
    }


def _make_deezer(root: Path, rows: list[dict[str, Any]], *, with_cover: bool = True) -> Path:
    folder = root / "deezer"
    (folder / "clips").mkdir(parents=True)
    (folder / "covers").mkdir()
    for row in rows:
        (folder / "clips" / row["audio"]).write_bytes(b"mp3")
        if with_cover:
            (folder / "covers" / row["cover"]).write_bytes(b"\xff\xd8\xffjpg")
    write_jsonl(folder / "catalog-tracks.jsonl", rows)
    return folder


def _deezer_unit(**extra: Any) -> RealUnit:
    fields: dict[str, Any] = {
        "key": "track:deezer-1",
        "domain": "music",
        "title": "t",
        "year": None,
        "genres": ("rock",),
    }
    return RealUnit(**(fields | extra))


class TestDeezer:
    def test_rows_keep_only_tracks_with_clip_and_find_cover(self, tmp_path: Path) -> None:
        rows = [_deezer_row(1), _deezer_row(2, album_id=8), _deezer_row(3)]
        folder = _make_deezer(tmp_path, rows)
        (folder / "clips" / "3.mp3").unlink()  # клип не докачан - трек отбрасывается
        (folder / "covers" / "8.jpg").unlink()  # обложки нет - трек остаётся без неё
        loaded = load_track_rows(tmp_path)
        assert [r["key"] for r in loaded] == ["track:deezer-1", "track:deezer-2"]
        assert loaded[0]["cover"] == str(folder / "covers" / "7.jpg")
        assert loaded[1]["cover"] is None

    def test_missing_folder_gives_no_rows(self, tmp_path: Path) -> None:
        assert load_track_rows(tmp_path / "nope") == []

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("hiphop", "hip-hop"), ("Hip Hop", "hip-hop"), ("rock", "rock"), (" Jazz ", "jazz")],
    )
    def test_genre_names_are_normalized(self, raw: str, expected: str) -> None:
        assert normalize_genre(raw) == expected

    def test_build_units_reads_deezer_with_cover_popularity_and_source(
        self, tmp_path: Path
    ) -> None:
        _make_deezer(tmp_path, [_deezer_row(1), _deezer_row(2, album_id=8)])
        units = {u.key: u for u in build_units(tmp_path)}
        first = units["track:deezer-1"]
        assert first.domain == "music"
        assert first.genres == ("rock",)
        assert first.popularity == 900001
        assert first.audio == tmp_path / "deezer" / "clips" / "1.mp3"
        assert first.cover == tmp_path / "deezer" / "covers" / "7.jpg"
        assert first.source_url == "https://www.deezer.com/track/1"
        assert first.license == ""
        assert units["track:deezer-2"].cover == tmp_path / "deezer" / "covers" / "8.jpg"

    def test_track_without_cover_file_has_no_cover(self, tmp_path: Path) -> None:
        _make_deezer(tmp_path, [_deezer_row(1)], with_cover=False)
        (unit,) = build_units(tmp_path)
        assert unit.cover is None

    def test_tracks_limit(self, tmp_path: Path) -> None:
        _make_deezer(tmp_path, [_deezer_row(n) for n in range(1, 6)])
        assert len(build_units(tmp_path, tracks_limit=2)) == 2

    def test_import_rows_contain_tracks_without_service_fields(self, tmp_path: Path) -> None:
        _make_deezer(tmp_path, [_deezer_row(1, "hiphop")])
        films, tracks = import_rows(tmp_path, build_units(tmp_path))
        assert films == []
        (row,) = tracks
        assert row["key"] == "track:deezer-1" and row["genres"] == ["hip-hop"]
        # поля выгрузки (источник, обложка, файл, лицензия) в строку импорта каталога не уходят
        assert not {"origin", "cover", "audio", "source_url", "license", "album"} & set(row)
        assert row["metrics"] == {"duration_ms": 30000, "popularity": 900001}


def _tmdb_row(
    media: str,
    tmdb_id: int,
    category: str,
    *,
    popularity: float = 10.0,
    genres: Sequence[str] = ("драма",),
) -> dict[str, Any]:
    """Строка ``tmdb/catalog-films.jsonl`` (формат ``seeds/media/fetch_tmdb_popular.py``)."""
    key = f"film:tmdb-{tmdb_id}" if media == "movie" else f"film:tmdb-tv-{tmdb_id}"
    return {
        "key": key,
        "kind": "film",
        "category": category,
        "media_type": media,
        "title": f"{media}{tmdb_id}",
        "original_title": f"o{tmdb_id}",
        "year": 2011,
        "persons": [{"name": "П", "role": "director" if media == "movie" else "creator"}],
        "genres": list(genres),
        "metrics": {"vote_average": 8.0, "popularity": popularity, "seasons": 2},
        "external_refs": {"tmdb": str(tmdb_id), "tmdb_type": media},
        "overview": "Сюжет",
        "folder": f"{category}/{media}-{tmdb_id}",
        "poster": "poster.jpg",
        "frames": [f"frame-{n}.jpg" for n in range(1, 7)],
    }


def _make_tmdb(
    root: Path, rows: list[dict[str, Any]], *, skip_files: frozenset[str] = frozenset()
) -> Path:
    tmdb = root / "tmdb"
    for row in rows:
        folder = tmdb / row["folder"]
        folder.mkdir(parents=True)
        if row["key"] in skip_files:
            continue
        for name in ("poster.jpg", *row["frames"]):
            (folder / name).write_bytes(b"\xff\xd8\xffjpg")
    write_jsonl(tmdb / "catalog-films.jsonl", rows)
    return tmdb


_CATEGORY_ROWS = [
    _tmdb_row("movie", 550, "Фильмы", popularity=50.0),
    _tmdb_row("movie", 551, "Фильмы", popularity=70.0),
    _tmdb_row("tv", 550, "Сериалы", popularity=60.0),  # тот же числовой id, что у фильма 550
    _tmdb_row("tv", 1399, "Сериалы", popularity=90.0),
]


class TestTmdbCategories:
    def test_reads_files_through_folder_for_every_category(self, tmp_path: Path) -> None:
        _make_tmdb(tmp_path, _CATEGORY_ROWS)
        units = {u.key: u for u in build_units(tmp_path)}
        assert set(units) == {
            "film:tmdb-550",
            "film:tmdb-551",
            "film:tmdb-tv-550",
            "film:tmdb-tv-1399",
        }
        series = units["film:tmdb-tv-550"]
        assert (series.category, series.media_type) == ("Сериалы", "tv")
        assert series.poster == tmp_path / "tmdb" / "Сериалы" / "tv-550" / "poster.jpg"
        assert [f.name for f in series.frames] == [f"frame-{n}.jpg" for n in range(1, 7)]
        assert series.frames[0].parent == series.poster.parent  # у сериала своя папка
        movie = units["film:tmdb-550"]
        assert (movie.category, movie.media_type, movie.popularity) == ("Фильмы", "movie", 50.0)
        assert movie.rating == 8.0

    def test_title_without_files_is_skipped(self, tmp_path: Path) -> None:
        _make_tmdb(tmp_path, _CATEGORY_ROWS, skip_files=frozenset({"film:tmdb-tv-1399"}))
        keys = {r["key"] for r in load_film_rows(tmp_path / "tmdb")}
        assert "film:tmdb-tv-1399" not in keys
        assert len(keys) == 3

    def test_folder_escaping_the_export_is_rejected(self, tmp_path: Path) -> None:
        row = _tmdb_row("movie", 1, "Фильмы") | {"folder": "../outside"}
        (tmp_path / "tmdb").mkdir()
        write_jsonl(tmp_path / "tmdb" / "catalog-films.jsonl", [row])
        assert load_film_rows(tmp_path / "tmdb") == []

    def test_legacy_row_without_folder_uses_id_folder(self, tmp_path: Path) -> None:
        row = _tmdb_row("movie", 7, "Фильмы")
        del row["folder"]
        folder = tmp_path / "tmdb" / "7"
        folder.mkdir(parents=True)
        for name in ("poster.jpg", *row["frames"]):
            (folder / name).write_bytes(b"jpg")
        write_jsonl(tmp_path / "tmdb" / "catalog-films.jsonl", [row])
        assert [r["key"] for r in load_film_rows(tmp_path / "tmdb")] == ["film:tmdb-7"]

    def test_import_rows_follow_catalog_contract(self, tmp_path: Path) -> None:
        _make_tmdb(tmp_path, _CATEGORY_ROWS)
        films, tracks = import_rows(tmp_path, build_units(tmp_path))
        assert tracks == []
        refs = {r["key"]: r["external_refs"]["tmdb"] for r in films}
        assert refs["film:tmdb-550"] == "550"
        assert refs["film:tmdb-tv-550"] == "tv-550"
        assert {p["role"] for r in films for p in r["persons"]} == {"director"}
        assert all("seasons" not in r["metrics"] for r in films)

    def test_manifest_for_series_matches_schema(self, tmp_path: Path) -> None:
        from jsonschema import Draft202012Validator

        _make_tmdb(tmp_path, _CATEGORY_ROWS)
        unit = next(u for u in build_units(tmp_path) if u.key == "film:tmdb-tv-1399")
        entry = PackageEntry(unit, "0192a3b4-c5d6-7e8f-9a0b-000000000101", unit_files(unit, None))
        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/packages/media_package_manifest.v1.json"
            ).read_text(encoding="utf-8")
        )
        manifest = package_manifest([entry])
        assert not list(Draft202012Validator(schema).iter_errors(manifest))
        item = manifest["items"][0]
        assert item["key"] == "film:tmdb-tv-1399"
        assert [f["role"] for f in item["files"]] == ["frame"] * 6 + ["poster"]

    def test_collections_by_category_from_fixture(self, tmp_path: Path) -> None:
        _make_tmdb(tmp_path, _CATEGORY_ROWS)
        specs = {s.key: s for s in film_collections(build_units(tmp_path), min_size=2)}
        assert "real-film-filmy-top" in specs
        assert "real-film-serialy-top" in specs
        assert next(i.seed_key for i in specs["real-film-top"].items) == "film:tmdb-tv-1399"
        assert [i.seed_key for i in specs["real-film-serialy-top"].items] == [
            "film:tmdb-tv-1399",
            "film:tmdb-tv-550",
        ]


class TestCoverFiles:
    def test_track_with_cover_uploads_audio_and_cover(self, tmp_path: Path) -> None:
        unit = _deezer_unit(audio=Path("a.mp3"), cover=Path("7.jpg"))
        files = unit_files(unit, tmp_path / "a.m4a")
        assert [(f.role, f.path.name, f.position) for f in files] == [
            ("audio", "a.m4a", None),
            ("cover", "7.jpg", None),
        ]

    def test_track_without_cover_uploads_audio_only(self, tmp_path: Path) -> None:
        files = unit_files(_track(1, "rock"), tmp_path / "a.m4a")
        assert [f.role for f in files] == ["audio"]

    def test_cover_is_offered_even_without_audio(self) -> None:
        """Аудио подготовлено не у всех единиц; обложка от него не зависит."""
        assert [f.role for f in unit_files(_deezer_unit(cover=Path("7.jpg")), None)] == ["cover"]

    def test_manifest_with_cover_matches_schema(self, tmp_path: Path) -> None:
        from jsonschema import Draft202012Validator

        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/packages/media_package_manifest.v1.json"
            ).read_text(encoding="utf-8")
        )
        unit = _deezer_unit(
            source_url="https://www.deezer.com/track/1",
            audio=Path("a.mp3"),
            cover=Path("7.jpg"),
        )
        entry = PackageEntry(
            unit, "0192a3b4-c5d6-7e8f-9a0b-000000000101", unit_files(unit, tmp_path / "a.m4a")
        )
        manifest = package_manifest([entry])
        assert not list(Draft202012Validator(schema).iter_errors(manifest))
        item = manifest["items"][0]
        assert [f["role"] for f in item["files"]] == ["audio", "cover"]
        assert item["files"][1]["path"] == "track-deezer-1/cover.jpg"
        assert item["origin"] == "upload"


class TestProvenance:
    def test_deezer_uses_upload_origin_and_source_without_license(self) -> None:
        unit = _deezer_unit(source_url="https://www.deezer.com/track/1")
        assert unit_provenance(unit) == {
            "origin": "upload",
            "source_url": "https://www.deezer.com/track/1",
        }

    def test_license_goes_to_note_when_present(self) -> None:
        unit = _deezer_unit(license="CC BY")
        assert unit_provenance(unit)["license_note"] == "CC BY (local, ADR 0014)"

    def test_track_without_source_url_has_only_origin(self) -> None:
        assert unit_provenance(_deezer_unit()) == {"origin": "upload"}

    def test_film_provenance_is_tmdb(self) -> None:
        provenance = unit_provenance(_film(550, 1999, 8.0))
        assert provenance["origin"] == "tmdb"
        assert provenance["source_url"] == "https://www.themoviedb.org/movie/550"

    def test_series_source_url_points_to_tv(self) -> None:
        unit = replace(_film(1399, 2011, 8.4), key="film:tmdb-tv-1399", media_type="tv")
        assert unit_provenance(unit)["source_url"] == "https://www.themoviedb.org/tv/1399"


# ---------------------------------------------------------------- без синтетики
def _spec(key: str, domain: str, size: int = 3, rank: int | None = None) -> CollectionSpec:
    return CollectionSpec(
        key=key,
        title=key,
        domain=domain,
        default_mode_key="m",
        items=tuple(CollectionItemSpec(seed_key=f"{domain}:{n}") for n in range(size)),
        featured_rank=rank,
    )


def _plan(
    key: str, domain: str | None, min_playable: int, *, period: str | None = None, **extra: Any
) -> ModePlan:
    return ModePlan(
        key=key,
        min_playable=min_playable,
        period=period,
        timezone="UTC",
        size=None,
        themed=False,
        domain=domain,
        **extra,
    )


class TestWithFeatured:
    def test_one_per_domain(self) -> None:
        specs = [
            _spec("real-music-rock", "music"),
            _spec("real-music-pop", "music"),
            _spec("real-film-80s", "film"),
            _spec("real-film-top", "film"),
        ]
        ranked = {s.key: s.featured_rank for s in with_featured(specs)}
        assert ranked == {
            "real-music-rock": 2,
            "real-music-pop": None,
            "real-film-80s": None,
            "real-film-top": 1,
        }

    def test_best_collection_of_each_domain_and_category_tops(self) -> None:
        specs = [
            _spec("real-music-pop", "music"),
            _spec("real-music-top", "music"),
            _spec("real-film-anime-top", "film"),
            _spec("real-film-top", "film"),
            _spec("real-film-serialy-top", "film"),
            _spec("real-film-serialy-drama", "film"),
        ]
        ranked = {s.key: s.featured_rank for s in with_featured(specs)}
        assert ranked == {
            "real-music-pop": None,
            "real-music-top": 2,
            "real-film-anime-top": 3,
            "real-film-top": 1,
            "real-film-serialy-top": 4,
            "real-film-serialy-drama": None,
        }

    def test_film_without_top_takes_first(self) -> None:
        ranked = with_featured([_spec("real-film-80s", "film"), _spec("real-film-00s", "film")])
        assert [s.featured_rank for s in ranked] == [1, None]

    def test_domain_without_collections_is_skipped(self) -> None:
        ranked = with_featured([_spec("real-music-rock", "music")])
        assert [s.featured_rank for s in ranked] == [2]

    def test_existing_rank_is_kept(self) -> None:
        specs = [_spec("a", "film", rank=7), _spec("real-film-top", "film")]
        assert [s.featured_rank for s in with_featured(specs)] == [7, None]

    def test_empty(self) -> None:
        assert with_featured([]) == []


def _config(**extra: Any) -> SeedConfig:
    return SeedConfig(
        identity_url="",
        catalog_url="",
        media_url="",
        game_url="",
        collections_url="",
        admin_login="",
        admin_password="",
        service_secret="",
        repo_root=Path(),
        **extra,
    )


def _standalone_seeder(units: list[RealUnit], playable: dict[str, set[str]] | None = None) -> Any:
    """RealSeeder без сети: проекция game и опрос подменены."""
    seeder = RealSeeder.__new__(RealSeeder)
    seeder._units = units
    seeder._cfg = _config(assets_timeout_s=1.0)
    seeder._echo = lambda _msg: None
    seeder._stall_timeout_s = 0.05
    seeder._pass_pause_s = 0.0
    seeder._keep_token = lambda: None
    ready = playable or {}

    def playable_ids(mode_key: str) -> set[str]:
        if mode_key not in ready:
            raise SeedError(f"режим {mode_key} не опубликован")
        return ready[mode_key]

    def poll(check: Any, **_kw: Any) -> None:
        if not check():
            raise SeedError("таймаут")

    seeder._playable_ids = playable_ids  # type: ignore[method-assign]
    seeder._poll = poll  # type: ignore[method-assign]
    return seeder


def _items(units: list[RealUnit]) -> dict[str, Any]:
    return {u.key: object() for u in units}


class TestPlanModes:
    def test_mode_without_material_is_skipped_not_fatal(self) -> None:
        films = [_film(n, 1990, 7.0) for n in range(6)]
        seeder = _standalone_seeder(films, {"film.daily": {str(n) for n in range(6)}})
        plans = [_plan("film.daily", "film", 5), _plan("music.daily", "music", 5)]
        ready, skipped = seeder.plan_modes(plans, _items(films))
        assert [p.key for p in ready] == ["film.daily"]
        assert skipped == [SkippedMode("music.daily", "нет единиц домена music")]

    def test_not_enough_units(self) -> None:
        films = [_film(n, 1990, 7.0) for n in range(3)]
        seeder = _standalone_seeder(films, {"film.daily": set()})
        ready, skipped = seeder.plan_modes([_plan("film.daily", "film", 5)], _items(films))
        assert ready == []
        assert skipped == [SkippedMode("film.daily", "единиц 3 из 5")]

    def test_units_missing_in_catalog_are_not_counted(self) -> None:
        films = [_film(n, 1990, 7.0) for n in range(6)]
        seeder = _standalone_seeder(films, {"film.daily": set()})
        ready, skipped = seeder.plan_modes([_plan("film.daily", "film", 5)], {})
        assert ready == []
        assert skipped[0].reason == "нет единиц домена film"

    def test_pool_not_playable_reports_reason(self) -> None:
        films = [_film(n, 1990, 7.0) for n in range(6)]
        seeder = _standalone_seeder(films, {"film.daily": {"a", "b"}})
        ready, skipped = seeder.plan_modes([_plan("film.daily", "film", 5)], _items(films))
        assert ready == []
        assert skipped == [SkippedMode("film.daily", "играбельных 2 из 5")]

    def test_mode_not_published_reports_reason(self) -> None:
        films = [_film(n, 1990, 7.0) for n in range(6)]
        seeder = _standalone_seeder(films, {})
        ready, skipped = seeder.plan_modes([_plan("film.daily", "film", 5)], _items(films))
        assert ready == []
        assert "не опубликован" in skipped[0].reason

    def test_mode_published_later_is_waited_for(self) -> None:
        films = [_film(n, 1990, 7.0) for n in range(6)]
        seeder = _standalone_seeder(films, {})
        attempts = {"n": 0}

        def playable_ids(_mode_key: str) -> set[str]:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise SeedError("режим не опубликован")  # game ещё не опубликовал сид
            return {str(n) for n in range(6)}

        seeder._playable_ids = playable_ids
        ready, skipped = seeder.plan_modes([_plan("film.daily", "film", 5)], _items(films))
        assert [p.key for p in ready] == ["film.daily"]
        assert skipped == []
        assert attempts["n"] == 3

    def test_mode_without_domain_counts_all_units(self) -> None:
        units = [_film(1, 1990, 7.0), _track(2, "rock")]
        seeder = _standalone_seeder(units, {"any": {"x", "y"}})
        ready, _skipped = seeder.plan_modes([_plan("any", None, 2)], _items(units))
        assert [p.key for p in ready] == ["any"]


class TestStandaloneSteps:
    def test_order(self) -> None:
        seeder = _standalone_seeder([])
        calls: list[str] = []
        daily = _plan("film.daily", "film", 1, period="day")
        lobby = _plan("film.lobby", "film", 1, online=True, uses_collection=True)
        missing = SkippedMode("music.daily", "нет единиц домена music")

        seeder.plan_modes = lambda _plans, _items: ([daily, lobby], [missing])
        seeder.wait_online_templates = lambda plans: calls.append(
            "templates:" + ",".join(p.key for p in plans)
        )
        seeder.seed_collections = lambda specs, _i: calls.append("collection:" + specs[0].key)
        seeder.wait_online_collections = lambda pairs, _i: calls.append("lobby:" + pairs[0][0].key)
        seeder.regenerate_period_sets = lambda plans: calls.append("period:" + plans[0].key)
        seeder.seed_progression = lambda: calls.append("progression")
        seeder.seed_events = lambda: calls.append("events")
        seeder.seed_social = lambda: calls.append("social")

        skipped = seeder.seed_standalone([daily, lobby], [_spec("real-film-top", "film", 5)], {})

        assert calls == [
            "templates:film.lobby",
            "collection:real-film-top",
            "lobby:film.lobby",
            "period:film.daily",
            "progression",
            "events",
            "social",
        ]
        assert skipped == [missing]

    def test_failures_go_to_summary(self) -> None:
        seeder = _standalone_seeder([])
        lobby = _plan("music.lobby", "music", 1, online=True, uses_collection=True)
        daily = _plan("film.daily", "film", 1, period="day")

        def boom(*_a: Any) -> None:
            raise SeedError("плохо")

        seeder.plan_modes = lambda plans, _items: (list(plans), [])
        seeder.wait_online_templates = boom
        seeder.seed_collections = boom
        seeder.regenerate_period_sets = boom
        seeder.seed_progression = lambda: None
        seeder.seed_events = lambda: None
        seeder.seed_social = lambda: None

        skipped = seeder.seed_standalone([lobby, daily], [_spec("real-film-top", "film")], {})

        # Шаблон лобби не опубликован: режим один раз в сводке и дальше не ждётся (лобби).
        assert [s.key for s in skipped] == [
            "music.lobby",
            "collection:real-film-top",
            "film.daily",
        ]


class _FakeSeeder:
    """Замена RealSeeder: записывает вызовы шагов в общий журнал."""

    log: list[str] = []  # noqa: RUF012 — общий журнал теста
    broken: dict[str, str] = {}  # noqa: RUF012
    specs: list[CollectionSpec] = []  # noqa: RUF012

    def __init__(self, *_a: Any, **_kw: Any) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        def step(*args: Any, **_kw: Any) -> Any:
            type(self).log.append(name)
            if name in {"mode_plans", "resolve_items"}:
                return [] if name == "mode_plans" else {}
            if name == "upload_real":
                return 0
            if name == "seed_standalone":
                type(self).specs = args[1]
                return [SkippedMode("music.daily", "нет единиц домена music")]
            return None

        return step


class TestRunSeedReal:
    def _run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, without_synthetic: bool
    ) -> tuple[list[str], dict[str, int], list[str]]:
        units = [_film(n, 1990, 8.0 - n / 10) for n in range(6)]
        _FakeSeeder.log = []
        monkeypatch.setattr(seed_real, "RealSeeder", _FakeSeeder)
        monkeypatch.setattr(seed_real, "build_units", lambda *_a, **_k: units)
        monkeypatch.setattr(seed_real, "prepare_audio", lambda *_a, **_k: ({}, {}))
        config = _config(catalog_dir=tmp_path / "catalog")
        lines: list[str] = []
        result = seed_real.run_seed_real(
            config,
            real_dir=tmp_path,
            ffmpeg="ffmpeg",
            min_collection_size=5,
            without_synthetic=without_synthetic,
            echo=lines.append,
        )
        return list(_FakeSeeder.log), result, lines

    def test_default_keeps_old_flow(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        log, result, lines = self._run(tmp_path, monkeypatch, without_synthetic=False)
        assert log == [
            "set_audio",
            "authenticate",
            "mode_plans",
            "import_catalog",
            "resolve_items",
            "upload_real",
            "drop_broken",
            "wait_profiles",
            "seed_collections",
            "regenerate_period_sets",
            "close",
        ]
        assert result["skipped"] == 0 and result["broken"] == 0
        assert not any("пропущено" in line for line in lines)

    def test_without_synthetic_runs_standalone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        log, result, lines = self._run(tmp_path, monkeypatch, without_synthetic=True)
        assert log == [
            "set_audio",
            "authenticate",
            "mode_plans",
            "import_catalog",
            "resolve_items",
            "upload_real",
            "drop_broken",
            "wait_profiles",
            "seed_standalone",
            "close",
        ]
        assert result["skipped"] == 1
        assert "seed-real: пропущено music.daily: нет единиц домена music" in lines
        assert any("рекомендуемые сборники" in line and "real-film-top" in line for line in lines)
        assert {s.key: s.featured_rank for s in _FakeSeeder.specs}["real-film-top"] == 1


# ---------------------------------------------------------------- масштаб: ожидание и битые единицы
class _Resp:
    def __init__(self, body: Any, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    def json(self) -> Any:
        return self._body


class _FakeMedia:
    """media-pipeline: ответы ``asset-sets`` по media_item_id, по очереди."""

    def __init__(self, scripts: dict[str, list[Any]]) -> None:
        self.scripts = scripts
        self.calls: dict[str, int] = {}

    def get(self, url: str, params: dict[str, str] | None = None) -> _Resp:
        assert url == "/admin/v1/asset-sets" and params is not None
        item = params["media_item_id"]
        self.calls[item] = self.calls.get(item, 0) + 1
        script = self.scripts[item]
        answer = script[min(self.calls[item], len(script)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return _Resp(answer)


def _sets(*statuses: str) -> list[dict[str, str]]:
    return [
        {"clue_type": f"c{n}", "profile_key": "default", "status": s}
        for n, s in enumerate(statuses)
    ]


def _waiting_seeder(scripts: dict[str, list[Any]], *, stall_s: float = 0.2) -> Any:
    seeder = RealSeeder.__new__(RealSeeder)
    seeder.lines = []  # type: ignore[attr-defined]
    seeder._media = _FakeMedia(scripts)  # type: ignore[assignment]
    seeder._echo = seeder.lines.append  # type: ignore[attr-defined]
    seeder._keep_token = lambda: None  # type: ignore[method-assign]
    seeder._check = lambda *_a, **_k: None  # type: ignore[method-assign]
    seeder._stall_timeout_s = stall_s
    seeder._pass_pause_s = 0.01
    seeder.broken = {}
    return seeder


class TestWaitUnitAssets:
    def test_progress_is_reported_and_all_ready_returns_count(self) -> None:
        scripts = {"a": [_sets("ready", "ready")], "b": [_sets("pending"), _sets("ready")]}
        seeder = _waiting_seeder(scripts)
        assert seeder._wait_unit_assets({"k:a": "a", "k:b": "b"}) == 2
        assert any("наборы готовы 2/2" in line for line in seeder.lines)
        assert seeder.broken == {}

    def test_failed_unit_is_skipped_and_listed_not_raised(self) -> None:
        scripts = {
            "good": [_sets("ready")],
            "bad": [[{"clue_type": "audio_intro", "profile_key": "default", "status": "failed",
                      "error_code": "source.undecodable"}]],
        }  # fmt: skip
        seeder = _waiting_seeder(scripts)
        assert seeder._wait_unit_assets({"k:good": "good", "k:bad": "bad"}) == 1
        assert set(seeder.broken) == {"k:bad"}
        assert "source.undecodable" in seeder.broken["k:bad"]
        assert any("проваленных 1" in line for line in seeder.lines)

    def test_stall_without_progress_times_out_with_keys(self) -> None:
        seeder = _waiting_seeder({"a": [_sets("ready")], "b": [_sets("pending")]}, stall_s=0.1)
        with pytest.raises(SeedError, match=r"нет прогресса.*k:b"):
            seeder._wait_unit_assets({"k:a": "a", "k:b": "b"})

    def test_progress_resets_the_stall_timer(self) -> None:
        """Общее время больше таймаута, но готовые наборы появляются - таймаута нет."""
        scripts = {f"u{n}": [_sets("pending")] * (n * 6) + [_sets("ready")] for n in range(1, 5)}
        seeder = _waiting_seeder(scripts, stall_s=0.12)
        pending = {f"k:{n}": n for n in scripts}
        assert seeder._wait_unit_assets(pending) == 4

    def test_transport_error_and_5xx_are_retried(self) -> None:
        import httpx

        scripts = {"a": [httpx.ConnectError("down"), _sets("ready")]}
        seeder = _waiting_seeder(scripts)
        assert seeder._wait_unit_assets({"k:a": "a"}) == 1

    def test_only_listed_units_are_waited_for(self) -> None:
        """Единицы вне списка засева (прошлые запуски) не опрашиваются и не мешают."""
        seeder = _waiting_seeder({"a": [_sets("ready")]})
        assert seeder._wait_unit_assets({"k:a": "a"}) == 1
        assert set(seeder._media.calls) == {"a"}  # type: ignore[attr-defined]


class TestWaitProfiles:
    def _seeder(self, profiles: list[Any]) -> Any:
        seeder = _waiting_seeder({})
        answers = iter(profiles)

        class Media:
            def get(self, _url: str) -> _Resp:
                return _Resp(next(answers))

        seeder._media = Media()
        return seeder

    def test_failed_sets_of_other_runs_are_ignored(self) -> None:
        profile = {"origin": "static", "clue_type": "x", "sets": {"failed": 7, "pending": 0}}
        seeder = self._seeder([[profile]])
        seeder.wait_profiles()
        assert any("failed: 7" in line for line in seeder.lines)

    def test_pending_sets_are_waited_for(self) -> None:
        busy = {"origin": "static", "clue_type": "x", "sets": {"failed": 0, "pending": 3}}
        done = {"origin": "static", "clue_type": "x", "sets": {"failed": 0, "pending": 0}}
        seeder = self._seeder([[busy], [busy | {"sets": {"failed": 0, "pending": 1}}], [done]])
        seeder.wait_profiles()

    def test_no_progress_in_profiles_times_out(self) -> None:
        busy = {"origin": "static", "clue_type": "x", "sets": {"failed": 0, "pending": 3}}
        seeder = self._seeder([[busy]] * 100)
        seeder._stall_timeout_s = 0.05
        with pytest.raises(SeedError, match="нет прогресса"):
            seeder.wait_profiles()


class TestUploadReal:
    """``upload_real``: пакеты с прогрессом, отвергнутый пакет и ошибки файлов не роняют засев."""

    def _seeder(self, units: list[RealUnit], outcomes: list[Any]) -> Any:
        seeder = _waiting_seeder({})
        seeder._units = units
        seeder._audio = {}
        seeder._package_units = 2
        seeder._uploaded = lambda _item: set()  # type: ignore[method-assign]
        answers = iter(outcomes)
        seeder.sent = []  # type: ignore[attr-defined]

        def send(batch: Any, label: str = "") -> set[str]:
            seeder.sent.append((label, [e.unit.key for e in batch]))
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer  # type: ignore[no-any-return]

        seeder._send_package = send  # type: ignore[method-assign]
        seeder._wait_unit_assets = len  # type: ignore[method-assign]
        return seeder

    @staticmethod
    def _units(n: int) -> tuple[list[RealUnit], dict[str, Any]]:
        units = [
            replace(_film(i, 1990, 5.0), frames=(Path("f1"),), poster=Path("p")) for i in range(n)
        ]
        return units, {
            u.key: type("Ref", (), {"media_item_id": f"id{i}"})() for i, u in enumerate(units)
        }

    def test_progress_label_and_failed_files(self) -> None:
        units, items = self._units(5)
        seeder = self._seeder(units, [set(), {"film:tmdb-2"}, set()])
        assert seeder.upload_real(items) == 4  # одна единица в broken - её не ждут
        assert [label for label, _ in seeder.sent] == ["1/3", "2/3", "3/3"]
        assert set(seeder.broken) == {"film:tmdb-2"}

    def test_rejected_package_marks_its_units_and_goes_on(self) -> None:
        units, items = self._units(5)
        seeder = self._seeder(units, [SeedError("package.too_big"), set(), set()])
        assert seeder.upload_real(items) == 3
        assert set(seeder.broken) == {"film:tmdb-0", "film:tmdb-1"}
        assert "package.too_big" in seeder.broken["film:tmdb-0"]

    def test_repeated_rejects_stop_the_seed(self) -> None:
        units, items = self._units(10)
        seeder = self._seeder(units, [SeedError("down")] * 10)
        with pytest.raises(SeedError, match="подряд"):
            seeder.upload_real(items)
        assert len(seeder.sent) == 3

    def test_already_uploaded_units_are_not_resent_but_still_waited(self) -> None:
        units, items = self._units(3)
        seeder = self._seeder(units, [])
        seeder._uploaded = lambda _item: {("frame", n) for n in range(1, 7)} | {("poster", 1)}  # type: ignore[method-assign]
        assert seeder.upload_real(items) == 3
        assert seeder.sent == []  # повторный запуск: ничего не отправляется


class TestReportBroken:
    def test_summary_lists_keys_and_writes_full_file(self, tmp_path: Path) -> None:
        broken = {f"track:deezer-{n}": "source.undecodable" for n in range(60)}
        lines: list[str] = []
        report_broken(broken, tmp_path, lines.append)
        assert any("60" in line for line in lines)
        assert sum("track:deezer-" in line for line in lines) == 50
        assert any("ещё 10" in line for line in lines)
        written = (tmp_path / "broken-units.txt").read_text(encoding="utf-8").splitlines()
        assert len(written) == 60

    def test_nothing_broken_prints_nothing(self, tmp_path: Path) -> None:
        lines: list[str] = []
        report_broken({}, tmp_path, lines.append)
        assert lines == [] and not (tmp_path / "broken-units.txt").exists()


# ---------------------------------------------------------------- правки по ревью
class TestWaitPackageResilience:
    def test_transport_error_and_5xx_do_not_fail_the_package(self) -> None:
        import httpx

        done = {
            "status": "done", "total": 2, "done": 2, "failed": 0,
            "missing_source": 0, "missing_license": 0, "errors": [],
        }  # fmt: skip
        answers: list[Any] = [httpx.ConnectError("down"), _Resp({}, 503), _Resp(done)]

        class Media:
            def get(self, _url: str) -> _Resp:
                answer = answers.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return answer  # type: ignore[no-any-return]

        seeder = _waiting_seeder({})
        seeder._media = Media()
        seeder._package_timeout_s = 1.0

        def poll(check: Any, **_kw: Any) -> None:
            for _ in range(10):
                if check():
                    return
            raise SeedError("таймаут")

        seeder._poll = poll
        assert seeder._wait_package("pkg") == set()
        assert answers == []


class TestAssetsDisabled:
    def test_disabled_set_does_not_block_readiness(self) -> None:
        seeder = _waiting_seeder({"a": [_sets("ready", "disabled")]})
        assert seeder._wait_unit_assets({"k:a": "a"}) == 1

    def test_only_disabled_sets_keep_waiting(self) -> None:
        seeder = _waiting_seeder({"a": [_sets("disabled")]}, stall_s=0.05)
        with pytest.raises(SeedError, match="нет прогресса"):
            seeder._wait_unit_assets({"k:a": "a"})


class TestRobustRows:
    def test_genres_with_same_slug_form_one_collection(self) -> None:
        units = [_track(i, "k-pop", listens=i) for i in range(12)]
        units += [_track(100 + i, "k pop", listens=i) for i in range(12)]
        specs = music_collections(units, min_size=20)
        assert [s.key for s in specs] == ["real-music-top", "real-music-k-pop"]
        assert len(specs[1].items) == 24

    def test_person_with_empty_or_long_name(self) -> None:
        raw = {
            "key": "film:x",
            "kind": "film",
            "title": "x",
            "persons": [
                {"name": "  ", "role": "director"},
                {"name": "Я" * 300, "role": "director"},
            ],
        }
        (person,) = catalog_row(raw)["persons"]
        assert len(person["name"]) == 200


class TestBrokenUnitsAreExcluded:
    """``run_seed_real``: битые единицы не попадают в каталог импорта, items и сборники."""

    class Seeder:
        broken: dict[str, str] = {"film:tmdb-1": "наборы ассетов в статусе failed"}  # noqa: RUF012
        collected: list[Any] = []  # noqa: RUF012
        items_seen: list[Any] = []  # noqa: RUF012

        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def mode_plans(self) -> list[Any]:
            return []

        def resolve_items(self, keys: Any) -> dict[str, Any]:
            return {k: object() for k in keys}

        def upload_real(self, _items: Any) -> int:
            return 0

        def seed_collections(self, specs: Any, items: Any) -> None:
            type(self).collected = list(specs)
            type(self).items_seen = list(items)

        def __getattr__(self, _name: str) -> Any:
            return lambda *_a, **_k: None

    def test_broken_film_and_failed_audio_are_dropped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        units = [_film(n, 1990, 8.0 - n / 10, popularity=float(n)) for n in range(1, 8)]
        units.append(_track(9, "pop"))
        monkeypatch.setattr(seed_real, "RealSeeder", self.Seeder)
        monkeypatch.setattr(seed_real, "build_units", lambda *_a, **_k: units)
        monkeypatch.setattr(
            seed_real,
            "prepare_audio",
            lambda *_a, **_k: ({}, {"track:demo-9": "перекодирование аудио: ошибка"}),
        )
        lines: list[str] = []
        result = seed_real.run_seed_real(
            _config(catalog_dir=tmp_path / "catalog"),
            real_dir=tmp_path,
            ffmpeg="ffmpeg",
            min_collection_size=3,
            echo=lines.append,
        )
        assert "film:tmdb-1" not in self.Seeder.items_seen
        assert "track:demo-9" not in self.Seeder.items_seen
        members = {i.seed_key for spec in self.Seeder.collected for i in spec.items}
        assert "film:tmdb-1" not in members and "film:tmdb-2" in members
        assert result["broken"] == 2
        listed = (tmp_path / "seedpack" / "broken-units.txt").read_text(encoding="utf-8")
        assert "film:tmdb-1" in listed and "track:demo-9" in listed
        assert any("пропущено битых единиц: 2" in line for line in lines)
