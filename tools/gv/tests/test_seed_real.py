"""Тесты чистых функций ``gv dev seed-real``: разбор каталога, сборники, подготовка аудио."""

from __future__ import annotations

import json
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
    build_units,
    catalog_row,
    ffmpeg_command,
    film_collections,
    import_rows,
    load_jamendo_rows,
    music_collections,
    normalize_genre,
    package_manifest,
    prepare_audio,
    read_jsonl,
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


def _film(n: int, year: int, rating: float, genres: tuple[str, ...] = ()) -> RealUnit:
    return RealUnit(
        key=f"film:tmdb-{n}", domain="film", title=f"f{n}", year=year, genres=genres, rating=rating
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
    def test_music_collections_by_genre_sorted_by_listens_and_capped(self) -> None:
        units = [_track(i, "folk", listens=i) for i in range(50)]
        units += [_track(100 + i, "rock") for i in range(5)]
        specs = music_collections(units, min_size=20, max_size=40)
        assert [s.title for s in specs] == ["Фолк-подборка"]
        keys = [i.seed_key for i in specs[0].items]
        assert len(keys) == 40
        assert keys[0] == "track:demo-49"
        assert specs[0].default_mode_key == "music.collection.intro"

    def test_music_collections_limit_and_unknown_genre_title(self) -> None:
        units = [_track(g * 100 + i, f"g{g}") for g in range(8) for i in range(21)]
        specs = music_collections(units, min_size=20, limit=6)
        assert len(specs) == 6
        assert len({s.key for s in specs}) == 6
        assert specs[0].title.startswith("Жанр: ")

    def test_film_collections(self) -> None:
        units = [_film(i, 1985, 5 + i / 10) for i in range(6)]
        units += [_film(10 + i, 2005, 7.0, ("мультфильм",)) for i in range(6)]
        units += [_film(20, 2020, 9.9)]
        specs = {s.key: s for s in film_collections(units)}
        assert set(specs) == {
            "real-film-top",
            "real-film-80s",
            "real-film-00s",
            "real-film-animation",
        }
        top = [i.seed_key for i in specs["real-film-top"].items]
        assert top[0] == "film:tmdb-20"
        assert len(specs["real-film-80s"].items) == 6
        assert specs["real-film-80s"].tags == ("eighties",)

    def test_small_film_collection_is_skipped(self) -> None:
        units = [_film(i, 1985, 5.0) for i in range(3)]
        assert {s.key for s in film_collections(units)} == set()


class TestAudio:
    def test_ffmpeg_command_loops_to_target_length(self) -> None:
        cmd = ffmpeg_command("ffmpeg", Path("in.mp3"), Path("out.m4a"))
        assert cmd[cmd.index("-stream_loop") + 1] == "-1"
        assert cmd[cmd.index("-t") + 1] == str(TARGET_AUDIO_S)
        assert cmd[-1] == "out.m4a"

    def test_prepare_audio_skips_ready_files(self, tmp_path: Path) -> None:
        units = [_track(1, "folk"), _track(2, "folk")]
        ready = audio_target(tmp_path, units[0].key)
        ready.parent.mkdir(parents=True)
        ready.write_bytes(b"m4a")
        calls: list[list[str]] = []

        def fake(cmd: object) -> None:
            calls.append(list(cmd))  # type: ignore[call-overload]  # тестовый раннер ffmpeg

        result = prepare_audio(units, tmp_path, "ffmpeg", fake)
        assert set(result) == {u.key for u in units}
        assert len(calls) == 1
        assert calls[0][-1] == str(audio_target(tmp_path, units[1].key))


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


# ---------------------------------------------------------------- Jamendo и обложки (ADR 0073)
def _jamendo_row(track_id: int, genre: str = "rock", *, album_id: int = 7) -> dict[str, Any]:
    """Строка ``jamendo/catalog-tracks.jsonl`` (формат ``seeds/media/fetch_jamendo.py``)."""
    return {
        "key": f"track:jamendo-{track_id}",
        "kind": "track",
        "title": f"j{track_id}",
        "year": 2015,
        "persons": [{"name": "Artist", "role": "artist"}],
        "genres": [genre, "dance"],
        "metrics": {"duration_ms": 30000},
        "album": "Album",
        "license": "http://creativecommons.org/licenses/by-nc-sa/3.0/",
        "source_url": f"https://www.jamendo.com/track/{track_id}",
        "origin": "jamendo",
        "audio": f"{track_id}.mp3",
        "cover": f"{album_id}.jpg",
    }


def _make_jamendo(root: Path, rows: list[dict[str, Any]], *, with_cover: bool = True) -> Path:
    folder = root / "jamendo"
    (folder / "clips").mkdir(parents=True)
    (folder / "covers").mkdir()
    for row in rows:
        (folder / "clips" / row["audio"]).write_bytes(b"mp3")
        if with_cover:
            (folder / "covers" / row["cover"]).write_bytes(b"\xff\xd8\xffjpg")
    write_jsonl(folder / "catalog-tracks.jsonl", rows)
    return folder


def _jamendo_unit(**extra: Any) -> RealUnit:
    fields: dict[str, Any] = {
        "key": "track:jamendo-1",
        "domain": "music",
        "title": "t",
        "year": None,
        "genres": ("rock",),
    }
    return RealUnit(**(fields | extra))


class TestJamendo:
    def test_rows_keep_only_tracks_with_clip_and_find_cover(self, tmp_path: Path) -> None:
        rows = [_jamendo_row(1), _jamendo_row(2, album_id=8), _jamendo_row(3)]
        folder = _make_jamendo(tmp_path, rows)
        (folder / "clips" / "3.mp3").unlink()  # клип не докачан — трек отбрасывается
        (folder / "covers" / "8.jpg").unlink()  # обложки нет — трек остаётся без неё
        loaded = load_jamendo_rows(folder)
        assert [r["key"] for r in loaded] == ["track:jamendo-1", "track:jamendo-2"]
        assert loaded[0]["cover"] == "7.jpg"
        assert loaded[1].get("cover") is None

    def test_missing_folder_gives_no_rows(self, tmp_path: Path) -> None:
        assert load_jamendo_rows(tmp_path / "nope") == []

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("hiphop", "hip-hop"), ("Hip Hop", "hip-hop"), ("rock", "rock"), (" Jazz ", "jazz")],
    )
    def test_genre_names_are_normalized(self, raw: str, expected: str) -> None:
        assert normalize_genre(raw) == expected

    def test_rows_normalize_genres(self, tmp_path: Path) -> None:
        folder = _make_jamendo(tmp_path, [_jamendo_row(1, "hiphop")])
        assert load_jamendo_rows(folder)[0]["genres"] == ["hip-hop", "dance"]

    def test_build_units_adds_jamendo_with_cover_and_source_fields(self, tmp_path: Path) -> None:
        _make_jamendo(tmp_path, [_jamendo_row(1), _jamendo_row(2, album_id=8)])
        units = {u.key: u for u in build_units(tmp_path)}
        first = units["track:jamendo-1"]
        assert first.domain == "music"
        assert first.genres == ("rock", "dance")
        assert first.audio == tmp_path / "jamendo" / "clips" / "1.mp3"
        assert first.cover == tmp_path / "jamendo" / "covers" / "7.jpg"
        assert first.source_url == "https://www.jamendo.com/track/1"
        assert first.license.endswith("by-nc-sa/3.0/")
        assert units["track:jamendo-2"].cover == tmp_path / "jamendo" / "covers" / "8.jpg"

    def test_tracks_limit_applies_per_source(self, tmp_path: Path) -> None:
        _make_jamendo(tmp_path, [_jamendo_row(n) for n in range(1, 6)])
        assert len(build_units(tmp_path, tracks_limit=2)) == 2

    def test_import_rows_contain_jamendo_tracks_without_service_fields(
        self, tmp_path: Path
    ) -> None:
        _make_jamendo(tmp_path, [_jamendo_row(1, "hiphop")])
        films, tracks = import_rows(tmp_path, build_units(tmp_path))
        assert films == []
        (row,) = tracks
        assert row["key"] == "track:jamendo-1" and row["genres"] == ["hip-hop", "dance"]
        # поля выгрузки (источник, обложка, файл, лицензия) в строку импорта каталога не уходят
        assert not {"origin", "cover", "audio", "source_url", "license", "album"} & set(row)
        assert row["metrics"] == {"duration_ms": 30000}


class TestCoverFiles:
    def test_track_with_cover_uploads_audio_and_cover(self, tmp_path: Path) -> None:
        unit = _jamendo_unit(audio=Path("a.mp3"), cover=Path("7.jpg"))
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
        assert [f.role for f in unit_files(_jamendo_unit(cover=Path("7.jpg")), None)] == ["cover"]

    def test_manifest_with_cover_matches_schema(self, tmp_path: Path) -> None:
        from jsonschema import Draft202012Validator

        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts/packages/media_package_manifest.v1.json"
            ).read_text(encoding="utf-8")
        )
        unit = _jamendo_unit(
            source_url="https://www.jamendo.com/track/1",
            license="http://creativecommons.org/licenses/by/3.0/",
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
        assert item["files"][1]["path"] == "track-jamendo-1/cover.jpg"
        assert item["origin"] == "upload"


class TestProvenance:
    def test_jamendo_uses_upload_origin_with_source_and_license(self) -> None:
        unit = _jamendo_unit(
            source_url="https://www.jamendo.com/track/1",
            license="http://creativecommons.org/licenses/by-nc-sa/3.0/",
        )
        assert unit_provenance(unit) == {
            "origin": "upload",
            "source_url": "https://www.jamendo.com/track/1",
            "license_note": (
                "Jamendo, http://creativecommons.org/licenses/by-nc-sa/3.0/ (local, ADR 0014)"
            ),
        }

    def test_jamendo_without_source_url_omits_it(self) -> None:
        assert "source_url" not in unit_provenance(_jamendo_unit())

    def test_film_provenance_is_tmdb(self) -> None:
        assert unit_provenance(_film(550, 1999, 8.0))["origin"] == "tmdb"


class TestJamendoGenres:
    def test_all_fetch_genres_have_collection_titles(self) -> None:
        for genre in ("jazz", "classical", "metal", "reggae", "blues", "ambient", "lounge"):
            assert genre in GENRE_TITLES, genre

    def test_hiphop_from_both_sources_form_one_collection(self) -> None:
        units = [_track(n, "hip-hop", listens=n) for n in range(1, 11)]
        units += [
            _jamendo_unit(key=f"track:jamendo-{n}", genres=(normalize_genre("hiphop"),))
            for n in range(1, 11)
        ]
        (spec,) = music_collections(units, min_size=20)
        assert spec.title == "Хип-хоп" and len(spec.items) == 20


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

        def poll(check: Any, **_kw: Any) -> None:
            for _ in range(5):
                if check():
                    return
            raise SeedError("таймаут")

        seeder._playable_ids = playable_ids
        seeder._poll = poll
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
        monkeypatch.setattr(seed_real, "prepare_audio", lambda *_a, **_k: {})
        config = _config(catalog_dir=tmp_path / "catalog")
        lines: list[str] = []
        result = seed_real.run_seed_real(
            config,
            real_dir=tmp_path,
            ffmpeg="ffmpeg",
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
            "wait_profiles",
            "seed_collections",
            "regenerate_period_sets",
            "close",
        ]
        assert result["skipped"] == 0
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
            "wait_profiles",
            "seed_standalone",
            "close",
        ]
        assert result["skipped"] == 1
        assert "seed-real: пропущено music.daily: нет единиц домена music" in lines
        assert any("рекомендуемые сборники" in line and "real-film-top" in line for line in lines)
        assert {s.key: s.featured_rank for s in _FakeSeeder.specs}["real-film-top"] == 1
