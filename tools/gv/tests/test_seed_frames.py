"""Замена кадров (``gv dev seed-real --replace-frames``): порядок кадров, снимки, ожидание."""

from __future__ import annotations

import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from gv_cli.main import app
from gv_cli.seed import SeedError
from gv_cli.seed_frames import (
    FIXED_CATALOG,
    REPLACE_REASON,
    FixedFilm,
    FrameReplacer,
    RunFiles,
    frame_winner_problem,
    load_fixed_films,
    plan_summary,
    run_replace_frames,
    select_films,
    sets_verdict,
)
from gv_cli.seed_real import (
    PackageEntry,
    package_manifest,
    unit_files,
    write_jsonl,
    write_package,
)

pytestmark = pytest.mark.unit

NAMES = ["frame-3.jpg", "frame-1.jpg", "frame-6.jpg", "frame-2.jpg", "frame-5.jpg", "frame-4.jpg"]
T0 = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _row(key: str, names: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    tmdb_id = key.rsplit("-", 1)[-1]
    return {
        "key": key,
        "kind": "film",
        "media_type": "tv" if "tmdb-tv-" in key else "movie",
        "folder": f"Фильмы/movie-{tmdb_id}",
        "frames": names if names is not None else NAMES,
        **extra,
    }


def _make(root: Path, rows: list[dict[str, Any]]) -> Path:
    """Каталог исправленных кадров: файлы по ``frames`` каждой строки + jsonl."""
    for row in rows:
        folder = root / row["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        for name in row["frames"]:
            (folder / Path(name).name).write_bytes(b"\xff\xd8\xff" + name.encode())
    write_jsonl(root / FIXED_CATALOG, rows)
    return root


def _films(n: int) -> list[FixedFilm]:
    return [FixedFilm(f"film:tmdb-{i}", "movie", (Path(f"{i}.jpg"),) * 6) for i in range(n)]


# ---------------------------------------------------------------- список тайтлов
class TestLoadFixedFilms:
    def test_frame_order_is_the_list_order_not_the_name_order(self, tmp_path: Path) -> None:
        _make(tmp_path, [_row("film:tmdb-1")])
        (film,), rejected = load_fixed_films(tmp_path)
        assert rejected == {}
        assert [
            p.name for p in film.frames
        ] == NAMES  # не отсортированы; лучший (frame-4) последний

    def test_manifest_and_archive_keep_the_list_order(self, tmp_path: Path) -> None:
        _make(tmp_path, [_row("film:tmdb-1")])
        (film,), _ = load_fixed_films(tmp_path)
        unit = film.unit()
        entry = PackageEntry(unit, "id-1", unit_files(unit, None))
        (item,) = package_manifest([entry])["items"]
        assert [(f["role"], f["position"]) for f in item["files"]] == [
            ("frame", n) for n in range(1, 7)
        ]  # только кадры: постера и аудио нет
        assert item["origin"] == "tmdb"
        archive = tmp_path / "package.zip"
        write_package(archive, [entry])
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            assert [zf.read(n) for n in names[1:]] == [b"\xff\xd8\xff" + n.encode() for n in NAMES]
        # порядок файлов в архиве = порядок манифеста = порядок обработки media-pipeline
        assert names[1:] == [f["path"] for f in item["files"]]

    def test_bad_rows_are_rejected_with_reason(self, tmp_path: Path) -> None:
        rows = [
            _row("film:tmdb-1"),
            _row("film:tmdb-2", NAMES[:5]),
            _row("film:tmdb-3", [*NAMES[:5], NAMES[0]]),
            _row("film:tmdb-4", folder="../evil"),
            _row("film:tmdb-5"),
            _row("film:tmdb-1"),
        ]
        _make(tmp_path, [r for r in rows if ".." not in r["folder"]])
        write_jsonl(tmp_path / FIXED_CATALOG, rows)
        (tmp_path / "Фильмы/movie-5" / NAMES[2]).unlink()
        films, rejected = load_fixed_films(tmp_path)
        assert [f.key for f in films] == ["film:tmdb-1"]
        assert "5 кадров вместо 6" in rejected["film:tmdb-2"]
        assert "повторяются" in rejected["film:tmdb-3"]
        assert rejected["film:tmdb-4"] == "недопустимая папка тайтла"
        assert "нет файла кадра" in rejected["film:tmdb-5"]

    def test_missing_catalog_is_an_error(self, tmp_path: Path) -> None:
        with pytest.raises(SeedError, match="нет каталога"):
            load_fixed_films(tmp_path)


class TestSelectFilms:
    def test_limit_counts_only_unfinished_units(self) -> None:
        films = _films(5)
        assert [f.key for f in select_films(films, limit=2)] == ["film:tmdb-0", "film:tmdb-1"]
        done = {"film:tmdb-0", "film:tmdb-1"}
        assert [f.key for f in select_films(films, done=done, limit=2)] == [
            "film:tmdb-2",
            "film:tmdb-3",
        ]

    def test_only_filters_and_unknown_key_fails(self) -> None:
        films = _films(3)
        assert [f.key for f in select_films(films, only=["film:tmdb-2"])] == ["film:tmdb-2"]
        with pytest.raises(SeedError, match="film:tmdb-9"):
            select_films(films, only=["film:tmdb-9"])

    def test_done_unit_stays_done_even_with_only(self) -> None:
        assert select_films(_films(2), only=["film:tmdb-1"], done={"film:tmdb-1"}) == []


# ---------------------------------------------------------------- проверки состояния
def _src(sid: str, position: int, at: datetime, *, origin: str = "tmdb") -> dict[str, Any]:
    return {
        "source_media_id": sid,
        "kind": "frame",
        "position": position,
        "origin": origin,
        "status": "ready",
        "created_at": at.isoformat(),
    }


def _old() -> list[dict[str, Any]]:
    return [_src(f"old{n}", n, T0 + timedelta(milliseconds=n)) for n in range(1, 7)]


def _new(step: timedelta = timedelta(milliseconds=1)) -> list[dict[str, Any]]:
    return [_src(f"new{n}", n, T0 + timedelta(hours=1) + step * n) for n in range(1, 7)]


class TestFrameWinner:
    BEFORE = frozenset(f"old{n}" for n in range(1, 7))

    def test_last_new_frame_wins(self) -> None:
        assert frame_winner_problem(_old() + _new(), before_ids=self.BEFORE, frames=6) is None

    def test_equal_upload_time_makes_frame_one_win_and_is_reported(self) -> None:
        same = _new(step=timedelta(0))
        problem = frame_winner_problem(_old() + same, before_ids=self.BEFORE, frames=6)
        assert problem is not None and "кадр 1" in problem

    def test_old_admin_source_keeps_winning(self) -> None:
        old = [*_old(), _src("seed1", 1, T0, origin="seed")]
        problem = frame_winner_problem(old + _new(), before_ids=self.BEFORE | {"seed1"}, frames=6)
        assert problem is not None and "старый" in problem

    def test_old_frame_left_on_a_position(self) -> None:
        new = [s for s in _new() if s["position"] != 3]
        problem = frame_winner_problem(_old() + new, before_ids=self.BEFORE, frames=6)
        assert problem is not None and "позиции 3" in problem

    def test_no_frames(self) -> None:
        assert (
            frame_winner_problem([], before_ids=(), frames=6) == "у единицы нет исходников-кадров"
        )


def _set(
    sid: str, gen: int, status: str = "ready", clue: str = "frame_progressive"
) -> dict[str, Any]:
    return {"asset_set_id": sid, "clue_type": clue, "profile_key": "default",
            "generation": gen, "status": status}  # fmt: skip


class TestSetsVerdict:
    BEFORE: ClassVar[dict[str, int]] = {"a": 1, "b": 1}

    def test_in_progress_until_every_set_is_ready(self) -> None:
        assert sets_verdict(self.BEFORE, [_set("a", 7, "pending"), _set("b", 1)]) is None
        assert sets_verdict(self.BEFORE, [_set("a", 7, "processing"), _set("b", 1)]) is None

    def test_old_ready_state_is_not_taken_for_done(self) -> None:
        verdict = sets_verdict(self.BEFORE, [_set("a", 1), _set("b", 1)])
        assert verdict and "не перегенерировались" in verdict

    def test_ready_after_generation_grew_everywhere(self) -> None:
        assert sets_verdict(self.BEFORE, [_set("a", 7), _set("b", 7)]) == ""

    def test_one_frame_set_that_did_not_regenerate_is_a_reason(self) -> None:
        verdict = sets_verdict(self.BEFORE, [_set("a", 7), _set("b", 1, clue="frame_sequence")])
        assert verdict and "frame_sequence/default" in verdict

    def test_new_set_is_fine_and_disabled_or_non_frame_sets_are_ignored(self) -> None:
        sets = [
            _set("a", 7),
            _set("c", 1, clue="frame_sequence"),
            _set("b", 1, "disabled"),
            _set("p", 1, clue="poster_masked"),
        ]
        assert sets_verdict({**self.BEFORE, "p": 1}, sets) == ""

    def test_unit_without_frame_sets_is_a_reason(self) -> None:
        verdict = sets_verdict({"p": 1}, [_set("p", 1, clue="poster_masked")])
        assert verdict and "нет кадровых наборов" in verdict

    def test_failed_set_is_a_reason(self) -> None:
        verdict = sets_verdict(self.BEFORE, [_set("a", 7, "failed"), _set("b", 1)])
        assert verdict and "failed" in verdict and "frame_progressive/default" in verdict


# ---------------------------------------------------------------- файлы запуска
class TestRunFiles:
    def test_state_snapshots_items_and_tee_survive_a_restart(self, tmp_path: Path) -> None:
        lines: list[str] = []
        first = RunFiles(tmp_path / "logs", "run1")
        first.tee(lines.append)("привет")
        first.mark_done("film:tmdb-1", "id1")
        first.write_snapshot({"key": "film:tmdb-1", "sets": [1]})
        first.write_snapshot({"key": "film:tmdb-1", "sets": [2]})  # середина замены - не берём
        first.save_items({"film:tmdb-1": "id1"})
        again = RunFiles(tmp_path / "logs", "run1")
        assert again.done_keys() == {"film:tmdb-1"}
        assert again.snapshots()["film:tmdb-1"]["sets"] == [1]  # первый снимок «до» главный
        assert again.load_items() == {"film:tmdb-1": "id1"}
        assert "привет" in again.log.read_text(encoding="utf-8")
        assert RunFiles(tmp_path / "logs", "other").done_keys() == set()

    def test_broken_list_is_written_only_when_there_are_errors(self, tmp_path: Path) -> None:
        files = RunFiles(tmp_path, "r")
        files.write_broken({})
        assert not files.broken.exists()
        files.write_broken({"film:tmdb-2": "причина", "film:tmdb-1": "другая"})
        assert files.broken.read_text(encoding="utf-8").splitlines() == [
            "film:tmdb-1\tдругая",
            "film:tmdb-2\tпричина",
        ]

    @pytest.mark.parametrize("bad", ["v1.2", "../x", "a/b", "", "x" * 41, "с пробелом", "a b"])
    def test_run_id_is_validated(self, tmp_path: Path, bad: str) -> None:
        with pytest.raises(SeedError, match="--run-id"):
            RunFiles(tmp_path, bad)

    def test_runs_with_similar_ids_never_share_files(self, tmp_path: Path) -> None:
        a, b = RunFiles(tmp_path, "v1-2"), RunFiles(tmp_path, "v1-3")
        a.mark_done("film:tmdb-1", "id1")
        assert b.done_keys() == set()
        assert len({a.state, a.snapshot, a.items, a.log, a.broken, a.attempts, b.state}) == 7
        assert all(p.parent == tmp_path for p in (a.state, a.snapshot, a.items, a.log))

    def test_attempts_are_counted_across_restarts(self, tmp_path: Path) -> None:
        assert RunFiles(tmp_path, "r").next_attempt() == 1
        assert RunFiles(tmp_path, "r").next_attempt() == 2
        assert RunFiles(tmp_path, "other").next_attempt() == 1

    def test_torn_last_line_does_not_break_resume(self, tmp_path: Path) -> None:
        files = RunFiles(tmp_path, "r")
        files.mark_done("film:tmdb-1", "id1")
        with files.state.open("a", encoding="utf-8") as handle:
            handle.write('{"key": "film:tmdb-2", "media_i')  # процесс упал посреди записи
        assert files.done_keys() == {"film:tmdb-1"}

    def test_items_are_written_atomically(self, tmp_path: Path) -> None:
        files = RunFiles(tmp_path, "r")
        files.save_items({"a": "1"})
        files.save_items({"a": "1", "b": "2"})
        assert files.load_items() == {"a": "1", "b": "2"}
        assert [p.name for p in tmp_path.glob("*.tmp")] == []


# ---------------------------------------------------------------- замена на «стенде»
class _Resp:
    status_code = 200

    def __init__(self, body: Any) -> None:
        self._body = body

    def json(self) -> Any:
        return self._body


class _Media:
    """media-pipeline: наборы и исходники единиц; пакет меняет их как настоящий сервис."""

    def __init__(self, ids: dict[str, str]) -> None:
        self.sets = {
            i: [_set(f"{i}-p", 1), _set(f"{i}-s", 1, clue="frame_sequence")] for i in ids.values()
        }
        self.sources = {i: _old() for i in ids.values()}
        self.calls: list[str] = []

    def get(self, url: str, params: dict[str, str]) -> _Resp:
        item = params["media_item_id"]
        self.calls.append(url)
        body = self.sets[item] if url.endswith("asset-sets") else self.sources[item]
        return _Resp(body)

    def apply_package(self, item: str, *, step: timedelta = timedelta(milliseconds=1)) -> None:
        """Загрузка шести кадров: свежие исходники, поколения +6, наборы в ready."""
        self.sources[item] = _old() + _new(step)
        for s in self.sets[item]:
            s["generation"] += 6


def _replacer(tmp_path: Path, ids: dict[str, str], media: _Media) -> Any:
    seeder = FrameReplacer.__new__(FrameReplacer)
    seeder.lines = []  # type: ignore[attr-defined]
    seeder._media = media  # type: ignore[assignment]
    seeder._echo = seeder.lines.append  # type: ignore[attr-defined]
    seeder._keep_token = lambda: None  # type: ignore[method-assign]
    seeder._check = lambda *_a, **_k: None  # type: ignore[method-assign]
    seeder._stall_timeout_s = 0.3
    seeder._pass_pause_s = 0.01
    seeder._package_units = 2
    seeder._package_concurrency = 1
    seeder._broken_lock = threading.Lock()
    seeder.broken = {}
    seeder.done = set()
    seeder._run_id = "run1"
    seeder._files = RunFiles(tmp_path / "logs", "run1")
    seeder._snapshots = seeder._files.snapshots()
    return seeder


class TestReplace:
    IDS: ClassVar[dict[str, str]] = {
        "film:tmdb-1": "id1",
        "film:tmdb-2": "id2",
        "film:tmdb-3": "id3",
    }

    def _run(
        self, tmp_path: Path, *, step: timedelta = timedelta(milliseconds=1), upload: bool = True
    ) -> tuple[Any, _Media, list[str]]:
        media = _Media(self.IDS)
        seeder = _replacer(tmp_path, self.IDS, media)
        order: list[str] = []

        def send(batch: Any, label: str = "") -> set[str]:
            # снимок «до» уже на диске к моменту отправки пакета
            order.extend(f"snapshot:{e.unit.key}" for e in batch if e.unit.key in seeder._snapshots)
            order.append(f"send:{label}")
            if upload:
                for entry in batch:
                    media.apply_package(entry.media_item_id, step=step)
            return set()

        seeder._send_package = send
        films = [FixedFilm(k, "movie", (Path("x.jpg"),) * 6) for k in self.IDS]
        seeder.replace(films, self.IDS)
        return seeder, media, order

    def test_snapshot_before_upload_then_wait_and_verify(self, tmp_path: Path) -> None:
        seeder, _, order = self._run(tmp_path)
        assert order == [
            "snapshot:film:tmdb-1", "snapshot:film:tmdb-2", "send:1/2",
            "snapshot:film:tmdb-3", "send:2/2",
        ]  # fmt: skip
        assert seeder.done == set(self.IDS) and seeder.broken == {}
        assert RunFiles(tmp_path / "logs", "run1").done_keys() == set(self.IDS)
        snap = RunFiles(tmp_path / "logs", "run1").snapshots()["film:tmdb-1"]
        assert snap["media_item_id"] == "id1"
        assert [(s["asset_set_id"], s["generation"], s["status"]) for s in snap["sets"]] == [
            ("id1-p", 1, "ready"),
            ("id1-s", 1, "ready"),
        ]
        assert len(snap["sources"]) == 6  # исходники «до»: по ним откат сверяет, что было

    def test_unit_whose_sets_did_not_regenerate_is_broken_not_done(self, tmp_path: Path) -> None:
        seeder, _, _ = self._run(tmp_path, upload=False)
        assert seeder.done == set()
        assert set(seeder.broken) == set(self.IDS)
        assert all("не перегенерировались" in r for r in seeder.broken.values())
        assert RunFiles(tmp_path / "logs", "run1").done_keys() == set()

    def test_wrong_winner_is_broken(self, tmp_path: Path) -> None:
        seeder, _, _ = self._run(tmp_path, step=timedelta(0))
        assert set(seeder.broken) == set(self.IDS)
        assert all("кадр 1" in r for r in seeder.broken.values())

    def test_failed_files_skip_waiting_and_are_reported(self, tmp_path: Path) -> None:
        media = _Media(self.IDS)
        seeder = _replacer(tmp_path, self.IDS, media)
        seeder._send_package = lambda _batch, _label="": {"film:tmdb-1"}
        films = [FixedFilm("film:tmdb-1", "movie", (Path("x.jpg"),) * 6)]
        seeder.replace(films, self.IDS)
        assert seeder.broken == {"film:tmdb-1": "ошибка файла в пакете"}
        assert seeder.done == set()

    def test_resume_keeps_the_first_snapshot(self, tmp_path: Path) -> None:
        # прерванный запуск: снимок снят, пакет ушёл наполовину (поколения уже выросли)
        media = _Media(self.IDS)
        first = _replacer(tmp_path, self.IDS, media)
        first._snapshot("film:tmdb-1", "id1")
        media.apply_package("id1")
        again = _replacer(tmp_path, self.IDS, media)
        assert again._snapshot("film:tmdb-1", "id1")["sets"][0]["generation"] == 1
        lines = (tmp_path / "logs" / "replace-frames-run1.snapshot.jsonl").read_text("utf-8")
        assert len(lines.splitlines()) == 1

    def test_each_attempt_has_its_own_reason_and_package_key(self, tmp_path: Path) -> None:
        # Повтор с тем же run-id обязан дойти до media-pipeline: причина и ключ входят в ключи
        # идемпотентности пакета и файлов, одинаковые вернули бы сохранённый ответ без загрузки.
        def make(attempt: int) -> Any:
            seeder = FrameReplacer.__new__(FrameReplacer)
            seeder._run_id = "run1"
            seeder._attempt = attempt
            seeder._package_reason = f"{REPLACE_REASON}: run1, попытка {attempt}"
            return seeder

        first, second = make(1), make(2)
        assert first._package_key("a" * 64) != second._package_key("a" * 64)
        assert first._package_reason != second._package_reason
        assert REPLACE_REASON in first._package_reason and len(first._package_reason) <= 500
        assert first._package_key("a" * 64) == make(1)._package_key("a" * 64)

    def test_rejected_batches_are_recorded_before_the_stop(self, tmp_path: Path) -> None:
        ids = {f"film:tmdb-{n}": f"id{n}" for n in range(6)}
        media = _Media(ids)
        seeder = _replacer(tmp_path, ids, media)
        seeder._package_units = 2

        def reject(batch: Any, label: str = "") -> set[str]:
            raise SeedError("package.too_big")

        seeder._send_package = reject
        films = [FixedFilm(k, "movie", (Path("x.jpg"),) * 6) for k in ids]
        with pytest.raises(SeedError, match="системный сбой"):
            seeder.replace(films, ids)
        assert set(seeder.broken) == set(ids)  # все три пакета записаны в .broken.txt


class TestRunReplaceFrames:
    def test_dry_run_checks_files_and_touches_nothing(self, tmp_path: Path) -> None:
        frames = _make(tmp_path / "fixed", [_row("film:tmdb-1"), _row("film:tmdb-2", NAMES[:3])])
        out: list[str] = []
        result = run_replace_frames(
            None, frames_dir=frames, log_dir=tmp_path / "logs", dry_run=True, echo=out.append
        )
        assert result == {"selected": 1, "rejected": 1, "done": 0, "broken": 0}
        assert not (tmp_path / "logs").exists()
        assert any("film:tmdb-2" in line for line in out)
        assert any("dry-run" in line for line in out)

    def test_plan_summary_counts_files_and_tasks(self, tmp_path: Path) -> None:
        _make(tmp_path, [_row("film:tmdb-1"), _row("film:tmdb-2")])
        films, _ = load_fixed_films(tmp_path)
        plan = plan_summary(films, 1)
        assert (plan["units"], plan["files"], plan["packages"]) == (2, 12, 2)
        assert (plan["sets"], plan["generation_tasks"]) == (6, 36)

    def test_nothing_to_do_does_not_need_a_stand(self, tmp_path: Path) -> None:
        frames = _make(tmp_path / "fixed", [_row("film:tmdb-1")])
        files = RunFiles(tmp_path / "logs", "r1")
        files.mark_done("film:tmdb-1", "id1")
        result = run_replace_frames(
            None, frames_dir=frames, log_dir=tmp_path / "logs", run_id="r1", echo=lambda _m: None
        )
        assert result["selected"] == 0


class TestCommand:
    runner = CliRunner()

    def test_flags_must_come_together(self, tmp_path: Path) -> None:
        only_dir = self.runner.invoke(app, ["dev", "seed-real", "--frames-dir", str(tmp_path)])
        only_flag = self.runner.invoke(app, ["dev", "seed-real", "--replace-frames"])
        stray = self.runner.invoke(app, ["dev", "seed-real", "--limit", "5"])
        assert {only_dir.exit_code, only_flag.exit_code, stray.exit_code} == {2}

    def test_dry_run_needs_no_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("GV_BOOTSTRAP_ADMIN__LOGIN", "GV_BOOTSTRAP_ADMIN__PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        frames = _make(tmp_path / "fixed", [_row("film:tmdb-1")])
        result = self.runner.invoke(
            app,
            ["dev", "seed-real", "--frames-dir", str(frames), "--replace-frames", "--dry-run",
             "--limit", "1", "--only", "film:tmdb-1", "--real-dir", str(tmp_path)],
        )  # fmt: skip
        assert result.exit_code == 0, result.output
        assert "dry-run" in result.output

    def test_replace_mode_rejects_seed_only_flags(self, tmp_path: Path) -> None:
        base = ["dev", "seed-real", "--frames-dir", str(tmp_path), "--replace-frames", "--dry-run"]
        for extra in (
            ["--without-synthetic"],
            ["--import-dir", str(tmp_path)],
            ["--tracks-limit", "5"],
        ):
            assert self.runner.invoke(app, [*base, *extra]).exit_code == 2

    def test_bad_run_id_is_an_error_not_a_traceback(self, tmp_path: Path) -> None:
        frames = _make(tmp_path / "fixed", [_row("film:tmdb-1")])
        result = self.runner.invoke(
            app,
            ["dev", "seed-real", "--frames-dir", str(frames), "--replace-frames", "--dry-run",
             "--run-id", "../x", "--real-dir", str(tmp_path)],
        )  # fmt: skip
        assert result.exit_code == 1 and "--run-id" in result.output


class TestPathsAndDryRunState:
    def test_symlink_pointing_outside_the_frames_dir_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"\xff\xd8\xffx")
        frames = _make(tmp_path / "fixed", [_row("film:tmdb-1")])
        link = frames / "Фильмы/movie-1" / NAMES[0]
        link.unlink()
        try:
            link.symlink_to(outside)
        except OSError:
            pytest.skip("символические ссылки недоступны")
        films, rejected = load_fixed_films(frames)
        assert films == [] and "вне каталога кадров" in rejected["film:tmdb-1"]

    def test_dry_run_sees_units_done_in_the_same_run(self, tmp_path: Path) -> None:
        frames = _make(tmp_path / "fixed", [_row("film:tmdb-1"), _row("film:tmdb-2")])
        RunFiles(tmp_path / "logs", "r1").mark_done("film:tmdb-1", "id1")
        result = run_replace_frames(
            None, frames_dir=frames, log_dir=tmp_path / "logs", run_id="r1", dry_run=True,
            echo=lambda _m: None,
        )  # fmt: skip
        assert result["selected"] == 1
