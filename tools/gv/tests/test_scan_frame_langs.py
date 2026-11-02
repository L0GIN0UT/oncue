"""Тесты скана кадров с языком (``seeds/media/scan_frame_langs.py``): только временные папки."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[3] / "seeds" / "media" / "scan_frame_langs.py"


@pytest.fixture(scope="module")
def scan() -> ModuleType:
    spec = importlib.util.spec_from_file_location("scan_frame_langs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _b(path: str, lang: str | None, vote: float) -> dict[str, Any]:
    return {"file_path": path, "iso_639_1": lang, "vote_average": vote}


def _clean(n: int) -> list[dict[str, Any]]:
    return [_b(f"/c{i}.jpg", None, 6.0 - i * 0.1) for i in range(n)]


def _row(mid: int) -> dict[str, Any]:
    return {
        "key": f"film:tmdb-{mid}",
        "media_type": "movie",
        "external_refs": {"tmdb": str(mid), "tmdb_type": "movie"},
        "folder": f"Фильмы/movie-{mid}",
    }


@pytest.fixture
def fake_download(scan: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Подмена скачивания кадров: пишет заглушку, афишу не повторяет."""
    written: list[Path] = []

    def download(_url: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpg")
        written.append(dest)
        return True

    monkeypatch.setattr(scan.ft, "download", download)
    monkeypatch.setattr(scan.ft, "repeats_poster", lambda *_a, **_k: False)
    return written


class TestLanguagePositions:
    def test_old_selection_reproduces_clean_first_then_language(self, scan: ModuleType) -> None:
        backdrops = [*_clean(4), _b("/en.jpg", "en", 9.0), _b("/ru.jpg", "ru", 1.0)]
        assert scan.language_positions(backdrops) == [5, 6]

    def test_no_language_when_six_clean_exist(self, scan: ModuleType) -> None:
        assert scan.language_positions([_b("/en.jpg", "en", 9.9), *_clean(6)]) == []


class TestProcess:
    def test_clean_film_is_ok_and_nothing_is_written(
        self, scan: ModuleType, tmp_path: Path, fake_download: list[Path]
    ) -> None:
        result = scan.process(
            _row(1), root=tmp_path, get_backdrops=lambda *_: _clean(7), min_vote=5.0
        )
        assert result["status"] == "ok"
        assert fake_download == []
        assert not (tmp_path / "tmdb-fixed").exists()

    def test_affected_film_gets_six_new_frames(
        self, scan: ModuleType, tmp_path: Path, fake_download: list[Path]
    ) -> None:
        # чистых только пять: шестой кадр старого выбора с языком
        backdrops = [*_clean(5), _b("/en.jpg", "en", 8.0), _b("/en2.jpg", "en", 7.0)]
        result = scan.process(
            _row(2), root=tmp_path, get_backdrops=lambda *_: backdrops, min_vote=5.0
        )
        assert result["status"] == "fixed"
        assert result["positions"] == [6]
        names = sorted(p.name for p in (tmp_path / "tmdb-fixed" / "Фильмы" / "movie-2").iterdir())
        assert names == [f"frame-{i}.jpg" for i in range(1, 7)]

    def test_unresolvable_film_is_marked_manual_and_leaves_no_files(
        self, scan: ModuleType, tmp_path: Path, fake_download: list[Path]
    ) -> None:
        backdrops = [*_clean(2), *[_b(f"/l{i}.jpg", "en", 9.0) for i in range(4)]]
        result = scan.process(
            _row(3), root=tmp_path, get_backdrops=lambda *_: backdrops, min_vote=5.0
        )
        assert result["status"] == "manual"
        assert fake_download  # что-то качалось, но комплект неполный
        assert not (tmp_path / "tmdb-fixed" / "Фильмы" / "movie-3").exists()

    def test_no_tmdb_data_is_not_recorded(self, scan: ModuleType, tmp_path: Path) -> None:
        assert (
            scan.process(_row(4), root=tmp_path, get_backdrops=lambda *_: None, min_vote=5.0)
            is None
        )


class TestReport:
    def test_only_affected_films_are_listed(self, scan: ModuleType) -> None:
        results = {
            "film:tmdb-1": {"key": "film:tmdb-1", "folder": "A/1", "positions": [], "status": "ok"},
            "film:tmdb-2": {
                "key": "film:tmdb-2",
                "folder": "A/2",
                "positions": [5, 6],
                "status": "fixed",
            },
            "film:tmdb-3": {
                "key": "film:tmdb-3",
                "folder": "A/3",
                "positions": [6],
                "status": "manual",
            },
        }
        lines = scan.report_lines(results, checked=3)
        assert lines[0].startswith("# проверено фильмов: 3, затронуто: 2")
        body = [line for line in lines if not line.startswith("#")]
        assert body == [
            "film:tmdb-2\tA/2\t5,6\tновые кадры: tmdb-fixed/A/2",
            f"film:tmdb-3\tA/3\t6\t{scan.MANUAL}",
        ]


class TestScan:
    def test_end_to_end_writes_only_report_state_and_fixed_frames(
        self,
        scan: ModuleType,
        tmp_path: Path,
        fake_download: list[Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tmdb = tmp_path / "tmdb"
        tmdb.mkdir()
        (tmdb / "deezer-marker.txt").write_text("не трогать", encoding="utf-8")
        rows = [_row(10), _row(11)]
        (tmdb / "catalog-films.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        data = {
            "10": _clean(6),
            "11": [*_clean(5), _b("/en.jpg", "en", 8.0), _b("/e2.jpg", "en", 7.0)],
        }
        monkeypatch.setattr(scan, "api_backdrops", lambda _media, mid: data[mid])
        before = sorted(p.name for p in tmdb.iterdir())

        scan.scan(tmp_path, limit=None, workers=2, min_vote=5.0)

        report = (tmdb / scan.REPORT_NAME).read_text(encoding="utf-8").split("\n")
        assert report[0].startswith("# проверено фильмов: 2, затронуто: 1")
        assert report[2].startswith("film:tmdb-11\t")
        assert sorted(p.name for p in tmdb.iterdir()) == sorted([*before, scan.REPORT_NAME])
        assert (tmp_path / "tmdb-fixed" / scan.STATE_NAME).is_file()
        # повторный запуск возобновляется и не запрашивает проверенное
        monkeypatch.setattr(scan, "api_backdrops", lambda *_: pytest.fail("повторный запрос"))
        scan.scan(tmp_path, limit=None, workers=2, min_vote=5.0)
