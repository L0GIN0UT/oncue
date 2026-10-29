"""Тесты чистых функций ``gv dev seed-real``: разбор каталога, сборники, подготовка аудио."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gv_cli.seed import SeedError
from gv_cli.seed_real import (
    TARGET_AUDIO_S,
    PackageEntry,
    PackageFile,
    RealSeeder,
    RealUnit,
    audio_target,
    catalog_row,
    ffmpeg_command,
    film_collections,
    load_track_rows,
    music_collections,
    package_manifest,
    prepare_audio,
    read_jsonl,
    split_batches,
    track_from_meta,
    unit_files,
    write_package,
)

pytestmark = pytest.mark.unit


def _track(n: int, genre: str, listens: int = 0) -> RealUnit:
    return RealUnit(
        key=f"track:fma-{n}",
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
            "key": "track:fma-2",
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


class TestTrackRows:
    def test_catalog_rows_without_clip_are_dropped(self, tmp_path: Path) -> None:
        clips = tmp_path / "clips"
        clips.mkdir()
        (clips / "000001.mp3").write_bytes(b"x")
        rows = [
            {"key": "track:fma-1", "title": "a", "audio": "000001.mp3"},
            {"key": "track:fma-2", "title": "b", "audio": "000002.mp3"},
        ]
        (tmp_path / "catalog-tracks.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
        )
        assert [r["key"] for r in load_track_rows(tmp_path)] == ["track:fma-1"]

    def test_track_from_meta_requires_title_artist_genre(self) -> None:
        meta = {
            "title": "",
            "artist": "a",
            "genre": "rock",
            "album": "",
            "license": "",
            "year": 1,
            "listens": 0,
        }
        assert track_from_meta(5, meta) is None
        built = track_from_meta(5, {**meta, "title": "t"})
        assert built is not None
        assert built["key"] == "track:fma-5"
        assert built["audio"] == "000005.mp3"


class TestCollections:
    def test_music_collections_by_genre_sorted_by_listens_and_capped(self) -> None:
        units = [_track(i, "folk", listens=i) for i in range(50)]
        units += [_track(100 + i, "rock") for i in range(5)]
        specs = music_collections(units, min_size=20, max_size=40)
        assert [s.title for s in specs] == ["Фолк-подборка"]
        keys = [i.seed_key for i in specs[0].items]
        assert len(keys) == 40
        assert keys[0] == "track:fma-49"
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
