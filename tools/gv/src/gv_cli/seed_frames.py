"""Замена кадров уже загруженных тайтлов (``gv dev seed-real --frames-dir ... --replace-frames``).

Исправленные кадры лежат в ``<frames-dir>/<папка тайтла>/`` рядом с каталогом
``catalog-films.fixed.jsonl`` (поле ``frames``). Команда грузит их пакетами в media-pipeline
тем же ``POST /admin/v1/packages``, что и ``seed-real``, но НЕ пропускает уже загруженные
``(kind, position)`` и не трогает каталог, сборники и режимы.

Как это работает в media-pipeline (проверено кодом и тестом ``test_packages.py``):

* каждый файл пакета — новый исходник (дедупликации по хэшу нет); старые исходники остаются;
* исходники обрабатываются в порядке манифеста, побеждает самый свежий (``created_at``);
* ``frame_progressive`` (``default`` и ``still``) берёт ОДИН лучший кадр вида ``frame`` независимо
  от позиции, то есть кадр, загруженный последним; ``frame_sequence`` берёт лучший кадр на
  каждой позиции 1..6;
* наборы не создаются заново: тот же ``asset_set_id``, ``generation`` растёт (на каждый файл
  пакета), на время генерации набор в ``pending``, объекты прошлых поколений удаляются.

Поэтому порядок кадров задаёт список ``frames`` в каталоге: позиция = номер в списке, «лучший»
кадр обязан стоять последним. Команда не сортирует файлы по имени и после генерации проверяет,
что победитель — последний кадр списка, а на каждой позиции лежит новый исходник.

Безопасность запуска:

* перед загрузкой для каждой единицы сохраняется снимок «до» (наборы с ``generation`` и
  ``status``, исходники) в ``replace-frames-<run-id>.snapshot.jsonl`` рядом с логом;
* запуск возобновляем с тем же ``--run-id``: готовые единицы (файл ``.state.jsonl``) пропускаются,
  снимки «до» не перезаписываются, карта ключ → ``media_item_id`` берётся из ``.items.json``;
* ``--limit N`` — «ещё N единиц» этого запуска (готовые не считаются), ``--only <ключ>`` —
  только эти единицы, ``--dry-run`` ничего не отправляет и не обращается к стенду.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from gv_cli.seed import FRAMES_PER_FILM, SeedConfig, SeedError
from gv_cli.seed_real import (
    PACKAGE_REJECTS_LIMIT,
    PACKAGE_UNITS,
    STALL_TIMEOUT_S,
    PackageEntry,
    RealSeeder,
    RealUnit,
    _film_folder,
    _media_type,
    read_jsonl,
    split_batches,
    unit_files,
)

FIXED_CATALOG = "catalog-films.fixed.jsonl"
REPLACE_REASON = "замена кадров tmdb-fixed"
# Статические кадровые профили фильма (``config/asset_profiles.yaml``): frame_progressive
# ``default`` и ``still``, frame_sequence ``default``. Динамические профили в оценку не входят.
SETS_PER_FILM = 3
# Происхождения исходника, которые media-pipeline считает «загруженными админом» (важнее прочих):
# зеркало ``services/media_pipeline/.../domain/sources.py::_ADMIN_ORIGINS``. Расхождение даст
# ложные вердикты проверки победителя; при правке серверной константы менять и эту.
_ADMIN_ORIGINS = frozenset({"upload", "seed"})
# Типы загадок, которые строятся из кадров: у каждого такого набора единицы поколение обязано
# вырасти после замены.
FRAME_CLUE_TYPES = frozenset({"frame_progressive", "frame_sequence"})
# Допустимый ``--run-id``: попадает в имена файлов и в ``Idempotency-Key``.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
# Статусы набора, при которых генерация ещё идёт.
_IN_PROGRESS = frozenset({"pending", "processing"})


# ---------------------------------------------------------------- список тайтлов
@dataclass(frozen=True, slots=True)
class FixedFilm:
    """Тайтл с исправленными кадрами.

    Attributes:
        key: Ключ строки каталога (``film:tmdb-550``).
        media_type: ``movie`` или ``tv`` (для ссылки на TMDB в происхождении).
        frames: Кадры В ПОРЯДКЕ СПИСКА ``frames`` (позиция = номер в списке с 1);
            лучший кадр последний.
    """

    key: str
    media_type: str
    frames: tuple[Path, ...]

    def unit(self) -> RealUnit:
        """Единица для сборки пакета: только кадры (постер и аудио не передаются)."""
        return RealUnit(
            key=self.key,
            domain="film",
            title=self.key,
            year=None,
            genres=(),
            media_type=self.media_type,
            frames=self.frames,
        )


def _row_frames(
    root: Path, folder: Path, row: Mapping[str, Any]
) -> tuple[tuple[Path, ...], str | None]:
    """Файлы кадров строки в порядке списка (все внутри ``root``); вторым значением — причина
    отказа."""
    names = [Path(str(n)).name for n in (row.get("frames") or ())]
    if len(names) != FRAMES_PER_FILM:
        # Обрезка отбросила бы последний кадр, то есть «лучший»: такую строку не берём.
        return (), f"в frames {len(names)} кадров вместо {FRAMES_PER_FILM}"
    if len(set(names)) != len(names):
        return (), "в frames повторяются имена файлов"
    paths = tuple(folder / n for n in names)
    for path in paths:
        if not path.is_file() or path.stat().st_size == 0:
            return (), f"нет файла кадра {path.name}"
        if not path.resolve().is_relative_to(root.resolve()):
            return (), f"кадр {path.name} вне каталога кадров (ссылка или путь наружу)"
    return paths, None


def load_fixed_films(frames_dir: Path) -> tuple[list[FixedFilm], dict[str, str]]:
    """Тайтлы ``catalog-films.fixed.jsonl`` по порядку файла и отказы (ключ → причина).

    Порядок кадров берётся из ``frames`` строки как есть, без сортировки по имени.
    """
    catalog = frames_dir / FIXED_CATALOG
    if not catalog.is_file():
        raise SeedError(f"нет каталога исправленных кадров: {catalog}")
    films: list[FixedFilm] = []
    rejected: dict[str, str] = {}
    seen: set[str] = set()
    for row in read_jsonl(catalog):
        key = str(row["key"])
        if key in seen:
            rejected[key] = "ключ повторяется в каталоге"
            continue
        seen.add(key)
        folder = _film_folder(frames_dir, row)
        if folder is None or not folder.resolve().is_relative_to(frames_dir.resolve()):
            rejected[key] = "недопустимая папка тайтла"
            continue
        frames, problem = _row_frames(frames_dir, folder, row)
        if problem is not None:
            rejected[key] = problem
            continue
        films.append(FixedFilm(key=key, media_type=_media_type(row), frames=frames))
    return films, rejected


def select_films(
    films: Sequence[FixedFilm],
    *,
    only: Collection[str] = (),
    done: Collection[str] = (),
    limit: int | None = None,
) -> list[FixedFilm]:
    """Что обрабатывать сейчас: ``only`` → без готовых → первые ``limit``; порядок каталога.

    Raises:
        SeedError: ключ из ``only`` отсутствует среди пригодных тайтлов каталога.
    """
    chosen = list(films)
    if only:
        known = {f.key for f in films}
        unknown = sorted(set(only) - known)
        if unknown:
            raise SeedError(f"--only: нет среди пригодных тайтлов каталога: {unknown}")
        chosen = [f for f in chosen if f.key in set(only)]
    finished = set(done)
    chosen = [f for f in chosen if f.key not in finished]
    return chosen if limit is None else chosen[:limit]


# ---------------------------------------------------------------- проверки состояния (чистые)
def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _priority(source: Mapping[str, Any]) -> tuple[int, datetime, int]:
    """Порядок выбора исходника в media-pipeline (``SourceMedia.priority``)."""
    admin = 1 if source.get("origin") in _ADMIN_ORIGINS else 0
    return admin, _parse_time(str(source["created_at"])), -int(source["position"])


def frame_winner_problem(
    sources: Iterable[Mapping[str, Any]], *, before_ids: Collection[str], frames: int
) -> str | None:
    """Проверка порядка: победитель ``frame_progressive`` — последний новый кадр.

    Повторяет выбор media-pipeline: админский источник важнее, затем свежее, затем меньшая
    позиция. Если часы дали одинаковое время, победит кадр 1, а не последний — это и ловится.

    Returns:
        ``None`` — всё верно, иначе причина.
    """
    usable = [s for s in sources if s.get("kind") == "frame" and s.get("status") != "failed"]
    if not usable:
        return "у единицы нет исходников-кадров"
    best = max(usable, key=_priority)
    if str(best["source_media_id"]) in before_ids:
        return "frame_progressive продолжит брать старый кадр (новые не победили по приоритету)"
    if int(best["position"]) != frames:
        return (
            f"frame_progressive возьмёт кадр {best['position']}, а не последний ({frames}): "
            "одинаковое время загрузки или другой порядок"
        )
    for position in range(1, frames + 1):
        at = [s for s in usable if int(s["position"]) == position]
        if not at or str(max(at, key=_priority)["source_media_id"]) in before_ids:
            return f"на позиции {position} остался старый кадр или кадра нет"
    return None


def sets_verdict(before: Mapping[str, int], sets: Sequence[Mapping[str, Any]]) -> str | None:
    """Готовность наборов единицы по поколению.

    Args:
        before: ``asset_set_id`` → ``generation`` до загрузки.
        sets: Ответ ``GET /admin/v1/asset-sets``.

    Returns:
        ``None`` — генерация ещё идёт; пустая строка — все наборы готовы, поколение выросло
        у каждого кадрового набора, существовавшего до замены (и хотя бы один кадровый набор
        есть); иначе причина поломки.
    """
    failed = sorted(
        f"{s.get('clue_type')}/{s.get('profile_key')}" for s in sets if s["status"] == "failed"
    )
    if failed:
        return f"наборы в статусе failed: {failed}"
    active = [s for s in sets if s["status"] != "disabled"]
    if any(s["status"] in _IN_PROGRESS for s in active):
        return None
    frame_sets = [s for s in active if s.get("clue_type") in FRAME_CLUE_TYPES]
    if not frame_sets:
        return "у единицы нет кадровых наборов"
    stale = sorted(
        f"{s.get('clue_type')}/{s.get('profile_key')}"
        for s in frame_sets
        if str(s["asset_set_id"]) in before
        and int(s["generation"]) <= before[str(s["asset_set_id"])]
    )
    if stale:
        return f"наборы не перегенерировались (поколения не выросли): {stale}"
    return ""


# ---------------------------------------------------------------- файлы запуска
class RunFiles:
    """Файлы запуска рядом с логом: журнал, снимки «до», готовые единицы, карта id."""

    def __init__(self, log_dir: Path, run_id: str) -> None:
        if not RUN_ID_RE.fullmatch(run_id):
            raise SeedError("--run-id: допустимы латиница, цифры, «-» и «_», до 40 символов")
        # Имена собираются целиком: ``with_suffix`` обрезал бы всё после точки в run-id.
        self.log = log_dir / f"replace-frames-{run_id}.log"
        self.snapshot = log_dir / f"replace-frames-{run_id}.snapshot.jsonl"
        self.state = log_dir / f"replace-frames-{run_id}.state.jsonl"
        self.items = log_dir / f"replace-frames-{run_id}.items.json"
        self.broken = log_dir / f"replace-frames-{run_id}.broken.txt"
        self.attempts = log_dir / f"replace-frames-{run_id}.attempts.jsonl"

    def next_attempt(self) -> int:
        """Номер попытки этого запуска (с 1): отметка пишется сразу, повтор получает следующий."""
        number = len(self._read_lines(self.attempts)) + 1
        self._append(
            self.attempts,
            json.dumps({"attempt": number, "at": datetime.now(UTC).isoformat()}),
        )
        return number

    def _append(self, path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line + "\n")

    def tee(self, echo: Callable[[str], None]) -> Callable[[str], None]:
        """Эхо, которое дублирует строки в журнал."""

        def write(message: str) -> None:
            echo(message)
            self._append(self.log, f"{datetime.now(UTC).isoformat(timespec='seconds')} {message}")

        return write

    def _read_lines(self, path: Path) -> list[dict[str, Any]]:
        """Строки JSONL; битая строка (обрыв записи при падении процесса) пропускается."""
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def done_keys(self) -> set[str]:
        """Единицы, уже заменённые в этом запуске."""
        return {str(r["key"]) for r in self._read_lines(self.state)}

    def mark_done(self, key: str, media_item_id: str) -> None:
        """Отметить единицу готовой."""
        line = {"key": key, "media_item_id": media_item_id, "at": datetime.now(UTC).isoformat()}
        self._append(self.state, json.dumps(line, ensure_ascii=False))

    def snapshots(self) -> dict[str, dict[str, Any]]:
        """Снимки «до»: ключ → запись (первая запись единицы не перезаписывается)."""
        result: dict[str, dict[str, Any]] = {}
        for row in self._read_lines(self.snapshot):
            result.setdefault(str(row["key"]), row)
        return result

    def write_snapshot(self, record: Mapping[str, Any]) -> None:
        """Дописать снимок «до»."""
        self._append(self.snapshot, json.dumps(record, ensure_ascii=False))

    def load_items(self) -> dict[str, str]:
        """Карта ключ → ``media_item_id`` прошлого запуска."""
        if not self.items.is_file():
            return {}
        data = json.loads(self.items.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}

    def save_items(self, items: Mapping[str, str]) -> None:
        """Сохранить карту ключ → ``media_item_id`` (атомарно: временный файл и ``os.replace``)."""
        self.items.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.items.with_name(self.items.name + ".tmp")
        tmp.write_text(json.dumps(dict(items), ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.items)

    def write_broken(self, broken: Mapping[str, str]) -> None:
        """Список единиц с ошибками (ключ, причина) для повторного запуска."""
        if not broken:
            return
        self.broken.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{key}\t{reason}" for key, reason in sorted(broken.items())]
        self.broken.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- замена
class FrameReplacer(RealSeeder):
    """Пакетная замена кадров с ожиданием по поколению наборов и проверкой победителя."""

    def __init__(
        self,
        config: SeedConfig,
        *,
        run_id: str,
        files: RunFiles,
        attempt: int = 1,
        package_units: int = PACKAGE_UNITS,
        stall_timeout_s: float = STALL_TIMEOUT_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            config, [], package_units=package_units, stall_timeout_s=stall_timeout_s, **kwargs
        )
        self._run_id = run_id
        self._attempt = attempt
        # Причина входит в ключи идемпотентности файлов на стороне media-pipeline: у каждой
        # попытки она своя, иначе повтор тех же файлов вернул бы сохранённый ответ без загрузки.
        self._package_reason = f"{REPLACE_REASON}: {run_id}, попытка {attempt}"
        self._files = files
        self._snapshots = files.snapshots()
        self.done: set[str] = set()

    def _package_key(self, digest: str) -> str:
        return f"replace-frames-{self._run_id}-a{self._attempt}-{digest[:24]}"

    # ------------------------------------------------------------ единицы стенда
    def resolve_ids(self, keys: Iterable[str]) -> tuple[dict[str, str], dict[str, str]]:
        """``media_item_id`` по ключам: из файла прошлого запуска, недостающие — одним обходом
        каталога стенда. Вернуть (найденные, ключи без единицы → причина)."""
        wanted = list(keys)
        known = self._files.load_items()
        if any(k not in known for k in wanted):
            self._echo("seed-real: поиск единиц в каталоге стенда (один обход)")
            seen: dict[str, Any] = {}
            self._collect_items(seen)
            known = {**known, **{k: ref.media_item_id for k, ref in seen.items()}}
            self._files.save_items(known)
        missing = {k: "единицы нет в каталоге стенда" for k in wanted if k not in known}
        return {k: known[k] for k in wanted if k in known}, missing

    def _get_json(
        self, url: str, params: Mapping[str, str], what: str
    ) -> list[dict[str, Any]] | None:
        """GET списка; ``None`` — временная недоступность (спросим снова)."""
        self._keep_token()
        try:
            resp = self._media.get(url, params=dict(params))
        except httpx.TransportError:
            return None
        if resp.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            return None
        self._check(resp, {200}, what)
        body: list[dict[str, Any]] = resp.json()
        return body

    def _snapshot(self, key: str, media_item_id: str) -> dict[str, Any]:
        """Снимок «до»: сохранённый прошлым запуском или новый (записывается в файл)."""
        if key in self._snapshots:
            return self._snapshots[key]
        params = {"media_item_id": media_item_id}
        sets = self._get_json("/admin/v1/asset-sets", params, f"наборы {key}")
        sources = self._get_json("/admin/v1/sources", params, f"исходники {key}")
        if sets is None or sources is None:
            raise SeedError(f"media-pipeline недоступен: снимок {key} не снят")
        record = {
            "key": key,
            "media_item_id": media_item_id,
            "taken_at": datetime.now(UTC).isoformat(),
            "sets": [
                {
                    "asset_set_id": str(s["asset_set_id"]),
                    "clue_type": s.get("clue_type"),
                    "profile_key": s.get("profile_key"),
                    "generation": int(s["generation"]),
                    "status": s["status"],
                }
                for s in sets
            ],
            "sources": [
                {
                    "source_media_id": str(s["source_media_id"]),
                    "kind": s.get("kind"),
                    "position": int(s["position"]),
                    "origin": s.get("origin"),
                    "created_at": s.get("created_at"),
                }
                for s in sources
            ],
        }
        self._files.write_snapshot(record)
        self._snapshots[key] = record
        return record

    # ------------------------------------------------------------ ожидание
    def _unit_verdict(self, key: str, media_item_id: str) -> str | None:
        """``None`` — в работе; пустая строка — единица заменена и проверена; иначе причина."""
        before = self._snapshots[key]
        params = {"media_item_id": media_item_id}
        sets = self._get_json("/admin/v1/asset-sets", params, f"наборы {key}")
        if sets is None:
            return None
        verdict = sets_verdict({s["asset_set_id"]: s["generation"] for s in before["sets"]}, sets)
        if verdict is None or verdict:
            return verdict
        sources = self._get_json("/admin/v1/sources", params, f"исходники {key}")
        if sources is None:
            return None
        problem = frame_winner_problem(
            sources,
            before_ids={s["source_media_id"] for s in before["sources"]},
            frames=FRAMES_PER_FILM,
        )
        return problem or ""

    def _wait_replaced(self, pending: dict[str, str]) -> None:
        """Ждать наборы единиц проходами; итог — в :attr:`done` и :attr:`broken`."""
        total = len(pending)

        def step() -> tuple[bool, float]:
            for key, item_id in list(pending.items()):
                verdict = self._unit_verdict(key, item_id)
                if verdict is None:
                    continue
                del pending[key]
                if verdict:
                    self.broken[key] = verdict
                else:
                    self.done.add(key)
                    self._files.mark_done(key, item_id)
            return not pending, float(total - len(pending))

        self._wait_progress(
            step,
            what="перегенерация наборов единиц",
            details=lambda: f"в работе {len(pending)}: {sorted(pending)[:5]}",
        )

    # ------------------------------------------------------------ основной цикл
    def replace(self, films: Sequence[FixedFilm], ids: Mapping[str, str]) -> None:
        """Заменить кадры тайтлов пакетами; ``ids`` — ключ → ``media_item_id``."""
        batches = split_batches(
            [
                PackageEntry(f.unit(), ids[f.key], unit_files(f.unit(), None))
                for f in films
                if f.key in ids
            ],
            self._package_units,
        )
        rejects = 0
        for number, batch in enumerate(batches, start=1):
            label = f"{number}/{len(batches)}"
            for entry in batch:
                self._snapshot(entry.unit.key, entry.media_item_id)
            failed, rejected = self._send_package_soft(batch, label)
            rejects = rejects + 1 if rejected else 0
            self.broken.update(failed)  # до проверки лимита: попадёт в .broken.txt
            if rejects >= PACKAGE_REJECTS_LIMIT:
                raise SeedError(
                    f"отвергнуто {rejects} пакета подряд — похоже на системный сбой: "
                    f"{next(iter(failed.values()))}"
                )
            pending = {e.unit.key: e.media_item_id for e in batch if e.unit.key not in failed}
            self._echo(f"seed-real: пакет {label} принят, ждём перегенерацию: {len(pending)} ед.")
            self._wait_replaced(pending)
            self._echo(f"seed-real: готово единиц {len(self.done)}, с ошибкой {len(self.broken)}")


# ---------------------------------------------------------------- план и запуск
def plan_summary(films: Sequence[FixedFilm], package_units: int) -> dict[str, int]:
    """Оценка объёма для ``--dry-run`` и шапки запуска."""
    files = sum(len(f.frames) for f in films)
    size = sum(p.stat().st_size for f in films for p in f.frames)
    return {
        "units": len(films),
        "files": files,
        "megabytes": size // (1024 * 1024),
        "packages": -(-len(films) // package_units),
        "sets": len(films) * SETS_PER_FILM,
        # Каждый файл пакета перепланирует набор: задач генерации в ~файлов × наборов, из них
        # реальных рендеров — по одному на набор (остальные устаревшие и пропускаются).
        "generation_tasks": files * SETS_PER_FILM,
    }


def run_replace_frames(
    config: SeedConfig | None,
    *,
    frames_dir: Path,
    log_dir: Path,
    run_id: str | None = None,
    only: Sequence[str] = (),
    limit: int | None = None,
    dry_run: bool = False,
    package_units: int = PACKAGE_UNITS,
    stall_timeout_s: float = STALL_TIMEOUT_S,
    echo: Callable[[str], None] = print,
) -> dict[str, int]:
    """Заменить кадры тайтлов из ``catalog-films.fixed.jsonl``; вернуть итоговые числа.

    Args:
        config: Адреса и учётка стенда; ``None`` допустим только с ``dry_run``.
        frames_dir: Каталог с ``catalog-films.fixed.jsonl`` и папками тайтлов.
        log_dir: Куда писать журнал, снимки «до», состояние и список ошибок.
        run_id: Идентификатор запуска; тот же ``run_id`` возобновляет прерванный запуск.
        only: Ключи тайтлов (пусто — все).
        limit: Не больше стольких ещё не готовых тайтлов за запуск.
        dry_run: Только проверить файлы и показать объём; стенд не трогается.
        package_units: Тайтлов в одном пакете.
        stall_timeout_s: Предел ожидания перегенерации без единого нового готового тайтла.
    """
    run = run_id or f"rf-{uuid.uuid4().hex[:10]}"
    files = RunFiles(log_dir, run)
    say = echo if dry_run else files.tee(echo)
    films, rejected = load_fixed_films(frames_dir)
    done = files.done_keys()  # чтение без побочных эффектов: dry-run видит тот же план
    chosen = select_films(films, only=only, done=done, limit=limit)
    plan = plan_summary(chosen, package_units)
    say(f"replace-frames: запуск {run}; в каталоге пригодных {len(films)}, отказов {len(rejected)}")
    for key, reason in sorted(rejected.items())[:20]:
        say(f"  ! {key}: {reason}")
    say(
        f"replace-frames: к замене {plan['units']} ед., файлов {plan['files']} "
        f"({plan['megabytes']} МБ), пакетов {plan['packages']}, наборов ≈ {plan['sets']}, "
        f"задач генерации ≈ {plan['generation_tasks']} (готовых в запуске пропущено {len(done)})"
    )
    summary = {"selected": plan["units"], "rejected": len(rejected), "done": 0, "broken": 0}
    if dry_run or not chosen:
        say(
            "replace-frames: dry-run, стенд не тронут"
            if dry_run
            else "replace-frames: нечего менять"
        )
        return summary
    if config is None:
        raise SeedError("для замены нужна конфигурация стенда")
    attempt = files.next_attempt()
    say(f"replace-frames: попытка {attempt}; для возобновления запустите с --run-id {run}")
    seeder = FrameReplacer(
        config, run_id=run, files=files, attempt=attempt, package_units=package_units,
        stall_timeout_s=stall_timeout_s, echo=say,
    )  # fmt: skip
    try:
        seeder.authenticate()
        ids, missing = seeder.resolve_ids(f.key for f in chosen)
        seeder.broken.update(missing)
        seeder.replace(chosen, ids)
    finally:
        seeder.close()
        files.write_broken(seeder.broken)
    say(f"replace-frames: заменено {len(seeder.done)}, с ошибкой {len(seeder.broken)}")
    if seeder.broken:
        say(f"replace-frames: список ошибок: {files.broken}; повторите с --run-id {run}")
    return {**summary, "done": len(seeder.done), "broken": len(seeder.broken)}
