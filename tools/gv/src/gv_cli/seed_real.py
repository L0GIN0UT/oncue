"""Засев реальными открытыми медиа (``gv dev seed-real``) — только local.

Дополняет синтетический ``gv dev seed`` (ничего не удаляет): берёт выгрузки вне репозитория
(``--real-dir``, по умолчанию ``C:/gv-media``; ADR 0014 — бинарные файлы и контент третьих лиц
в git не хранятся):

* ``tmdb/catalog-films.jsonl`` + ``tmdb/<id>/{poster,frame-1..6}.jpg`` — фильмы;
* ``fma/clips/<id>.mp3`` (+ ``fma/catalog-tracks.jsonl`` или ``fma/meta/.../tracks.csv``) — треки;
* ``jamendo/clips/<id>.mp3`` + ``jamendo/covers/<album_id>.jpg`` +
  ``jamendo/catalog-tracks.jsonl`` — треки с обложками (``seeds/media/fetch_jamendo.py``);
  обложка грузится ролью ``cover``, у треков FMA обложек нет (``artwork`` ревила ``null``).

Шаги: каталожные файлы импорта (``real-films.jsonl``, ``real-tracks.jsonl``) → импорт →
исходники (клипы перекодируются ffmpeg в 50 с, как синтетика) → ожидание наборов ассетов →
сборники по жанрам, рейтингу и десятилетиям → наборы периода. Повторный запуск безопасен:
ключи идемпотентности стабильны, уже загруженные исходники пропускаются.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from gv_cli.seed import (
    FRAMES_PER_FILM,
    REASON,
    CollectionItemSpec,
    CollectionSpec,
    ModePlan,
    SeedConfig,
    Seeder,
    SeedError,
    _ItemRef,
    lobby_collections,
)

REAL_FILMS_FILE = "real-films.jsonl"
REAL_TRACKS_FILE = "real-tracks.jsonl"
# Длина подготовленного аудио: профили читают ``start_s`` 30 (реверс) + до 15 с уровня.
TARGET_AUDIO_S = 50
# Размеры сборников: понятный диапазон для игры и верхний предел проекции.
MIN_COLLECTION_ITEMS = 20
MAX_COLLECTION_ITEMS = 40
MAX_MUSIC_COLLECTIONS = 6
# Через сколько секунд обновлять admin-токен (срок жизни короче засева).
TOKEN_REFRESH_S = 240.0
# Пакеты для media-pipeline: единиц в пакете, чтение файла при отправке, ожидание статуса.
PACKAGE_UNITS = 100
PACKAGE_CHUNK = 1024 * 1024
PACKAGE_UPLOAD_TIMEOUT_S = 600.0
PACKAGE_TIMEOUT_S = 3600.0
PACKAGE_ERRORS_SHOWN = 10
PACKAGE_TERMINAL = frozenset({"done", "partial", "failed"})
# Поля строки импорта каталога (``seeds/catalog/README.md``); остальное уходит в «extras».
_IMPORT_FIELDS = (
    "key",
    "kind",
    "title",
    "original_title",
    "year",
    "aliases",
    "persons",
    "genres",
    "external_refs",
    "metrics",
    "countries",
)
_ALLOWED_METRICS = frozenset(
    {"popularity", "vote_average", "vote_count", "revenue_usd", "runtime_min", "duration_ms"}
)
_MAX_PLOT = 5000
_MAX_TITLE = 300
_YEAR_MIN, _YEAR_MAX = 1900, 2030
# Происхождение исходников ``POST /admin/v1/sources`` и пометка лицензии.
ORIGIN_TRACK = "upload"
ORIGIN_FILM = "tmdb"
NOTE_FILM = "TMDB (local, ADR 0014); не для распространения"

# Названия, обложки и режимы сборников. Порядок обложек — по порядку жанров.
GENRE_TITLES: Mapping[str, str] = {
    "folk": "Фолк-подборка",
    "electronic": "Электроника",
    "rock": "Рок-подборка",
    "hip-hop": "Хип-хоп",
    "pop": "Поп-подборка",
    "experimental": "Эксперимент",
    "instrumental": "Инструментал",
    "international": "Музыка мира",
    "jazz": "Джаз",
    "classical": "Классика",
    "metal": "Метал",
    "reggae": "Регги",
    "blues": "Блюз",
    "ambient": "Эмбиент",
    "lounge": "Лаунж",
}
# Написания жанра из разных источников → общий ключ (у FMA ``hip-hop``, у Jamendo ``hiphop``).
GENRE_ALIASES: Mapping[str, str] = {"hiphop": "hip-hop", "hip hop": "hip-hop"}
MUSIC_COVERS = ("vinyl", "neon", "night", "sunset", "forest", "ocean")
MUSIC_MODE = "music.collection.intro"
FILM_MODE = "film.collection.frame"
# «Рекомендуемое» без синтетики: порядок по доменам и сборник, который предпочитаем в кино.
FEATURED_RANKS: Mapping[str, int] = {"film": 1, "music": 2}
FEATURED_FILM_KEY = "real-film-top"
# Сколько ждать пул одного режима без синтетики, прежде чем пропустить режим (в сводку):
# иначе N режимов без материала держали бы засев N × ``assets_timeout_s``.
PLAYABLE_WAIT_S = 300.0

# Типы колбэков для тестов: ffmpeg и копирование не выполняются в unit-тестах.
Runner = Callable[[Sequence[str]], None]


@dataclass(frozen=True, slots=True)
class RealUnit:
    """Единица реального засева: ключ, домен и её файлы на диске.

    Attributes:
        key: Ключ строки каталога (``film:tmdb-1``, ``track:fma-2``).
        domain: ``film``/``music``.
        title: Название (для сборников и отчёта).
        year: Год, если известен.
        genres: Жанры.
        rating: ``vote_average`` фильма; у трека — ``None``.
        popularity: Прослушивания трека; у фильма — ``None``.
        license: Лицензия трека (в ``license_note`` загрузки).
        provider: Откуда трек: ``fma`` (по умолчанию) или ``jamendo``.
        source_url: Страница трека у источника (в ``source_url`` загрузки); пусто — не передаётся.
        audio: Исходный клип трека.
        cover: Обложка альбома трека (роль ``cover``); ``None`` — обложки нет.
        poster: Постер фильма.
        frames: Кадры фильма по возрастанию позиции.
    """

    key: str
    domain: str
    title: str
    year: int | None
    genres: tuple[str, ...]
    rating: float | None = None
    popularity: int | None = None
    license: str = ""
    provider: str = "fma"
    source_url: str = ""
    audio: Path | None = None
    cover: Path | None = None
    poster: Path | None = None
    frames: tuple[Path, ...] = ()


# ---------------------------------------------------------------- разбор каталога
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Строки JSONL (пустые и ``#`` пропускаются); битая строка — :class:`SeedError`."""
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SeedError(f"{path.name}:{number}: не JSON") from exc
        if not isinstance(row, dict) or "key" not in row:
            raise SeedError(f"{path.name}:{number}: нет поля key")
        rows.append(row)
    return rows


def _clean(text: str) -> str:
    """Название без HTML-сущностей (``&amp;``) и лишних пробелов."""
    return html.unescape(text).strip()


def catalog_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Строка выгрузки → строка импорта каталога.

    Оставляет только поля импорта (неизвестные catalog считает ошибкой строки), чистит
    названия от HTML-сущностей, переносит ``listens`` в ``popularity`` и ``overview`` в
    ``clue_texts.plot_raw`` (текст загадки «описание»; маскируется самим catalog).
    """
    row: dict[str, Any] = {
        name: raw[name] for name in _IMPORT_FIELDS if raw.get(name) not in (None, [], {})
    }
    row["title"] = _clean(str(raw["title"]))[:_MAX_TITLE]
    if row.get("original_title"):
        row["original_title"] = _clean(str(row["original_title"]))[:_MAX_TITLE]
    if row.get("persons"):
        row["persons"] = [{**p, "name": _clean(str(p["name"]))} for p in row["persons"]]
    metrics = dict(raw.get("metrics") or {})
    if "listens" in metrics:
        metrics.setdefault("popularity", metrics["listens"])
    metrics = {k: v for k, v in metrics.items() if k in _ALLOWED_METRICS and v is not None}
    if metrics:
        row["metrics"] = metrics
    else:
        row.pop("metrics", None)
    overview = str(raw.get("overview") or "").strip()
    if overview:
        row["clue_texts"] = {"plot_raw": overview[:_MAX_PLOT]}
    return row


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    """Записать строки JSONL; вернуть их число."""
    lines = [json.dumps(r, ensure_ascii=False) for r in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def _fma_meta_rows(csv_path: Path, wanted: set[int]) -> dict[int, dict[str, Any]]:
    """Метаданные нужных треков из ``tracks.csv`` FMA (трёхстрочная шапка)."""
    result: dict[int, dict[str, Any]] = {}
    with csv_path.open(encoding="utf-8", newline="") as fh:
        rows = csv.reader(fh)
        top, sub = next(rows), next(rows)
        next(rows)
        idx = {(t, s): i for i, (t, s) in enumerate(zip(top, sub, strict=False))}
        for row in rows:
            if not row or not row[0].isdigit() or int(row[0]) not in wanted:
                continue
            released = row[idx[("album", "date_released")]]
            created = row[idx[("track", "date_created")]]
            year = (released or created or "")[:4]
            result[int(row[0])] = {
                "title": _clean(row[idx[("track", "title")]]),
                "artist": _clean(row[idx[("artist", "name")]]),
                "album": _clean(row[idx[("album", "title")]]),
                "genre": row[idx[("track", "genre_top")]].strip().lower(),
                "license": row[idx[("track", "license")]].strip(),
                "year": int(year) if year.isdigit() and _YEAR_MIN < int(year) < _YEAR_MAX else None,
                "listens": int(row[idx[("track", "listens")]] or 0),
            }
    return result


def track_from_meta(track_id: int, meta: Mapping[str, Any]) -> dict[str, Any] | None:
    """Строка выгрузки трека из метаданных FMA; нет названия/артиста/жанра — ``None``."""
    if not (meta["title"] and meta["artist"] and meta["genre"]):
        return None
    return {
        "key": f"track:fma-{track_id}",
        "kind": "track",
        "title": meta["title"],
        "year": meta["year"],
        "persons": [{"name": meta["artist"], "role": "artist"}],
        "genres": [meta["genre"]],
        "metrics": {"duration_ms": 30000, "listens": meta["listens"]},
        "album": meta["album"],
        "license": meta["license"],
        "audio": f"{track_id:06d}.mp3",
    }


def load_track_rows(fma_dir: Path) -> list[dict[str, Any]]:
    """Треки с клипами на диске: каталог выгрузки, а недостающее — из ``tracks.csv``.

    Выгрузка клипов идёт дольше записи каталога, поэтому строки без файла отбрасываются, а
    клипы без строки достраиваются по метаданным FMA.
    """
    clips = {int(p.stem): p for p in (fma_dir / "clips").glob("*.mp3") if p.stat().st_size > 0}
    catalog = fma_dir / "catalog-tracks.jsonl"
    rows = {
        int(str(r["key"]).rsplit("-", 1)[-1]): r
        for r in (read_jsonl(catalog) if catalog.is_file() else [])
    }
    rows = {tid: r for tid, r in rows.items() if tid in clips}
    missing = set(clips) - set(rows)
    csv_path = fma_dir / "meta" / "fma_metadata" / "tracks.csv"
    if missing and csv_path.is_file():
        for tid, meta in _fma_meta_rows(csv_path, missing).items():
            built = track_from_meta(tid, meta)
            if built is not None:
                rows[tid] = built
    return [rows[tid] for tid in sorted(rows)]


def normalize_genre(name: str) -> str:
    """Жанр в нижнем регистре и общем написании (``hiphop`` → ``hip-hop``)."""
    key = " ".join(name.lower().split())
    return GENRE_ALIASES.get(key, key)


def load_jamendo_rows(jamendo_dir: Path) -> list[dict[str, Any]]:
    """Треки Jamendo с клипами на диске; ``cover`` остаётся, только если файл обложки есть.

    Выгрузка (``seeds/media/fetch_jamendo.py``) может быть неполной: строки без клипа
    отбрасываются, трек без файла обложки идёт без неё (``artwork`` будет ``null``).
    Жанры приводятся к написанию FMA (``hiphop`` → ``hip-hop``).
    """
    catalog = jamendo_dir / "catalog-tracks.jsonl"
    if not catalog.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for row in read_jsonl(catalog):
        clip = jamendo_dir / "clips" / str(row.get("audio") or "")
        if not str(row.get("audio") or "") or not clip.is_file() or clip.stat().st_size == 0:
            continue
        cover = jamendo_dir / "covers" / str(row.get("cover") or "")
        has_cover = bool(row.get("cover")) and cover.is_file() and cover.stat().st_size > 0
        rows.append(
            {
                **row,
                "cover": row["cover"] if has_cover else None,
                "genres": [normalize_genre(str(g)) for g in row.get("genres") or ()],
            }
        )
    return rows


def load_film_rows(tmdb_dir: Path) -> list[dict[str, Any]]:
    """Фильмы выгрузки, у которых на диске есть постер и все кадры."""
    path = tmdb_dir / "catalog-films.jsonl"
    if not path.is_file():
        return []
    return [r for r in read_jsonl(path) if _film_files(tmdb_dir, r) is not None]


def _film_files(tmdb_dir: Path, row: Mapping[str, Any]) -> tuple[Path, tuple[Path, ...]] | None:
    """Постер и кадры фильма (``tmdb/<id>/``); неполный набор — ``None``."""
    folder = tmdb_dir / str(row["key"]).rsplit("-", 1)[-1]
    poster = folder / str(row.get("poster") or "poster.jpg")
    frames = tuple(folder / name for name in (row.get("frames") or ())[:FRAMES_PER_FILM])
    if (
        not poster.is_file()
        or len(frames) < FRAMES_PER_FILM
        or not all(f.is_file() for f in frames)
    ):
        return None
    return poster, frames


def build_units(real_dir: Path, *, tracks_limit: int | None = None) -> list[RealUnit]:
    """Все единицы засева с файлами; порядок — фильмы, затем треки по ключу."""
    tmdb_dir, fma_dir, jamendo_dir = real_dir / "tmdb", real_dir / "fma", real_dir / "jamendo"
    units: list[RealUnit] = []
    for row in load_film_rows(tmdb_dir):
        poster, frames = _film_files(tmdb_dir, row) or (None, ())
        units.append(
            RealUnit(
                key=str(row["key"]),
                domain="film",
                title=_clean(str(row["title"])),
                year=row.get("year"),
                genres=tuple(row.get("genres") or ()),
                rating=float((row.get("metrics") or {}).get("vote_average") or 0),
                poster=poster,
                frames=frames,
            )
        )
    for row in load_track_rows(fma_dir)[:tracks_limit]:
        metrics = row.get("metrics") or {}
        units.append(
            RealUnit(
                key=str(row["key"]),
                domain="music",
                title=_clean(str(row["title"])),
                year=row.get("year"),
                genres=tuple(row.get("genres") or ()),
                popularity=int(metrics.get("listens") or 0),
                license=str(row.get("license") or ""),
                audio=fma_dir / "clips" / str(row["audio"]),
            )
        )
    units.extend(
        RealUnit(
            key=str(row["key"]),
            domain="music",
            title=_clean(str(row["title"])),
            year=row.get("year"),
            genres=tuple(row.get("genres") or ()),
            license=str(row.get("license") or ""),
            provider="jamendo",
            source_url=str(row.get("source_url") or ""),
            audio=jamendo_dir / "clips" / str(row["audio"]),
            cover=jamendo_dir / "covers" / str(row["cover"]) if row.get("cover") else None,
        )
        for row in load_jamendo_rows(jamendo_dir)[:tracks_limit]
    )
    return units


def import_rows(
    real_dir: Path, units: Iterable[RealUnit]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Строки импорта (фильмы, треки) только для единиц ``units``."""
    keep = {u.key for u in units}
    films = [catalog_row(r) for r in load_film_rows(real_dir / "tmdb") if r["key"] in keep]
    track_rows = [*load_track_rows(real_dir / "fma"), *load_jamendo_rows(real_dir / "jamendo")]
    tracks = [catalog_row(r) for r in track_rows if r["key"] in keep]
    return films, tracks


# ---------------------------------------------------------------- аудио
def audio_target(seedpack: Path, key: str) -> Path:
    """Куда класть перекодированный клип единицы."""
    return seedpack / "audio" / f"{key.replace(':', '-')}.m4a"


def ffmpeg_command(ffmpeg: str, src: Path, dst: Path, seconds: int = TARGET_AUDIO_S) -> list[str]:
    """Команда: зациклить 30-секундный клип и обрезать до ``seconds`` в AAC/m4a.

    Профили media-pipeline читают ``start_s`` 30 (реверс) и до 15 с уровня — клип в 30 с им
    не хватает. Зацикливание даёт ровно ``seconds`` без тишины.
    """
    return [
        ffmpeg, "-y", "-loglevel", "error",
        "-stream_loop", "-1", "-i", str(src),
        "-t", str(seconds), "-vn", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(dst),
    ]  # fmt: skip


def _run(cmd: Sequence[str]) -> None:
    subprocess.run(list(cmd), check=True)


def prepare_audio(
    units: Iterable[RealUnit], seedpack: Path, ffmpeg: str, run: Runner = _run
) -> dict[str, Path]:
    """Перекодировать клипы треков; готовые файлы не трогает. Вернуть ключ → m4a."""
    result: dict[str, Path] = {}
    for unit in units:
        if unit.audio is None:
            continue
        dst = audio_target(seedpack, unit.key)
        if not dst.is_file() or dst.stat().st_size == 0:
            dst.parent.mkdir(parents=True, exist_ok=True)
            run(ffmpeg_command(ffmpeg, unit.audio, dst))
        result[unit.key] = dst
    return result


# ---------------------------------------------------------------- сборники
def _items(units: Iterable[RealUnit]) -> tuple[CollectionItemSpec, ...]:
    return tuple(CollectionItemSpec(seed_key=u.key) for u in units)


def music_collections(
    units: Iterable[RealUnit],
    *,
    min_size: int = MIN_COLLECTION_ITEMS,
    max_size: int = MAX_COLLECTION_ITEMS,
    limit: int = MAX_MUSIC_COLLECTIONS,
) -> list[CollectionSpec]:
    """Музыкальные сборники по основному жанру: самые крупные жанры, внутри — по прослушиваниям."""
    by_genre: dict[str, list[RealUnit]] = {}
    for unit in units:
        if unit.domain == "music" and unit.genres:
            by_genre.setdefault(unit.genres[0], []).append(unit)
    fitting = [(g, u) for g, u in by_genre.items() if len(u) >= min_size]
    fitting.sort(key=lambda gu: (-len(gu[1]), gu[0]))
    specs: list[CollectionSpec] = []
    for position, (genre, members) in enumerate(fitting[:limit]):
        members.sort(key=lambda u: (-(u.popularity or 0), u.key))
        title = GENRE_TITLES.get(genre, f"Жанр: {genre}")
        specs.append(
            CollectionSpec(
                key=f"real-music-{genre}",
                title=title,
                domain="music",
                default_mode_key=MUSIC_MODE,
                items=_items(members[:max_size]),
                description=f"Угадайте трек по вступлению: {genre}, открытая музыка.",
                cover=MUSIC_COVERS[position % len(MUSIC_COVERS)],
            )
        )
    return specs


@dataclass(frozen=True, slots=True)
class _FilmRule:
    """Правило киношного сборника: фильтр по годам и/или жанру (без фильтров — все)."""

    key: str
    title: str
    description: str
    cover: str
    tags: tuple[str, ...]
    years: tuple[int, int] | None = None
    genre: str | None = None

    def matches(self, unit: RealUnit) -> bool:
        if self.years is not None and (
            unit.year is None or not self.years[0] <= unit.year <= self.years[1]
        ):
            return False
        return self.genre is None or self.genre in unit.genres


def film_collections(
    units: Iterable[RealUnit],
    *,
    min_size: int = 5,
    max_size: int = MAX_COLLECTION_ITEMS,
) -> list[CollectionSpec]:
    """Киношные сборники: топ по рейтингу, 80-е, нулевые, мультфильмы (малые пропускаются)."""
    films = [u for u in units if u.domain == "film"]
    rules = [
        _FilmRule("top", "Топ по рейтингу", "Лучшие фильмы по оценке зрителей.", "cinema",
                  ("classic",)),
        _FilmRule("80s", "Фильмы 80-х", "Кино восьмидесятых по кадрам.", "sunset",
                  ("eighties",), years=(1980, 1989)),
        _FilmRule("00s", "Нулевые", "Кино 2000–2009 годов.", "night", (), years=(2000, 2009)),
        _FilmRule("animation", "Мультфильмы", "Анимация: от классики до новинок.", "popcorn",
                  ("animation",), genre="мультфильм"),
    ]  # fmt: skip
    specs: list[CollectionSpec] = []
    for rule in rules:
        members = sorted(
            (u for u in films if rule.matches(u)), key=lambda u: (-(u.rating or 0), u.key)
        )[:max_size]
        if len(members) < min_size:
            continue
        specs.append(
            CollectionSpec(
                key=f"real-film-{rule.key}",
                title=rule.title,
                domain="film",
                default_mode_key=FILM_MODE,
                items=_items(members),
                description=rule.description,
                tags=rule.tags,
                cover=rule.cover,
            )
        )
    return specs


def with_featured(specs: Sequence[CollectionSpec]) -> list[CollectionSpec]:
    """Отметить «рекомендуемым» один сборник каждого домена (без синтетики отметить некого).

    Кино — «Топ по рейтингу», если он есть, иначе первый киношный; музыка — первый
    (музыкальные уже отсортированы от крупных жанров). Сборник с уже заданным
    ``featured_rank`` не трогается.
    """
    chosen: dict[str, str] = {}
    for spec in specs:
        if spec.featured_rank is not None:
            chosen[spec.domain] = spec.key
    for domain in FEATURED_RANKS:
        if domain in chosen:
            continue
        of_domain = [s for s in specs if s.domain == domain]
        if of_domain:
            preferred = [s for s in of_domain if s.key == FEATURED_FILM_KEY]
            chosen[domain] = (preferred or of_domain)[0].key
    return [
        replace(s, featured_rank=FEATURED_RANKS[s.domain])
        if chosen.get(s.domain) == s.key and s.featured_rank is None
        else s
        for s in specs
    ]


@dataclass(frozen=True, slots=True)
class SkippedMode:
    """Режим или сборник, оставшийся без материала: попадает в итоговую сводку."""

    key: str
    reason: str


# ---------------------------------------------------------------- пакет для media-pipeline
@dataclass(frozen=True, slots=True)
class PackageFile:
    """Файл записи пакета: роль в манифесте, путь на диске и порядок среди исходников вида."""

    role: str
    path: Path
    position: int | None = None


@dataclass(frozen=True, slots=True)
class PackageEntry:
    """Запись пакета: единица, её id в каталоге и файлы к загрузке."""

    unit: RealUnit
    media_item_id: str
    files: tuple[PackageFile, ...]


def unit_files(unit: RealUnit, audio: Path | None) -> tuple[PackageFile, ...]:
    """Файлы единицы: аудио (перекодированное) и обложка трека или кадры 1..6 и постер фильма."""
    if unit.domain == "music":
        music = [PackageFile("audio", audio)] if audio is not None else []
        if unit.cover is not None:
            music.append(PackageFile("cover", unit.cover))
        return tuple(music)
    files = [PackageFile("frame", f, n) for n, f in enumerate(unit.frames, start=1)]
    if unit.poster is not None:
        files.append(PackageFile("poster", unit.poster))
    return tuple(files)


def unit_provenance(unit: RealUnit) -> dict[str, str]:
    """``origin``/``source_url``/``license_note`` записи манифеста (пустое не передаётся)."""
    if unit.domain == "music":
        if unit.provider == "jamendo":
            # Источник и лицензия — в своих полях, ``origin`` остаётся ``upload``: контракт
            # манифеста значения ``jamendo`` не знает и не расширяется.
            note = f"Jamendo, {unit.license or 'CC'} (local, ADR 0014)"
            result = {"origin": ORIGIN_TRACK, "license_note": note}
            if unit.source_url:
                result["source_url"] = unit.source_url
            return result
        note = f"Free Music Archive, {unit.license or 'CC'} (local, ADR 0014)"
        return {"origin": ORIGIN_TRACK, "license_note": note}
    tmdb_id = unit.key.rsplit("-", 1)[-1]
    return {
        "origin": ORIGIN_FILM,
        "source_url": f"https://www.themoviedb.org/movie/{tmdb_id}",
        "license_note": NOTE_FILM,
    }


def _arcname(unit: RealUnit, file: PackageFile) -> str:
    suffix = "" if file.position is None else f"-{file.position}"
    return f"{unit.key.replace(':', '-')}/{file.role}{suffix}{file.path.suffix.lower()}"


def package_manifest(entries: Sequence[PackageEntry]) -> dict[str, Any]:
    """``manifest.json`` по схеме ``contracts/packages/media_package_manifest.v1.json``."""
    return {
        "version": 1,
        "items": [
            {
                "key": e.unit.key,
                "kind": "track" if e.unit.domain == "music" else "film",
                "media_item_id": e.media_item_id,
                "files": [
                    {
                        "role": f.role,
                        "path": _arcname(e.unit, f),
                        **({"position": f.position} if f.position is not None else {}),
                    }
                    for f in e.files
                ],
                **unit_provenance(e.unit),
            }
            for e in entries
        ],
    }


def write_package(archive: Path, entries: Sequence[PackageEntry]) -> int:
    """Записать zip с манифестом; вернуть число файлов.

    ``ZIP_STORED``: JPEG и AAC уже сжаты, а высокая степень сжатия у media-pipeline считается
    признаком zip-бомбы.
    """
    count = 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("manifest.json", json.dumps(package_manifest(entries), ensure_ascii=False))
        for entry in entries:
            for file in entry.files:
                zf.write(file.path, _arcname(entry.unit, file))
                count += 1
    return count


def split_batches(entries: Sequence[PackageEntry], size: int) -> list[Sequence[PackageEntry]]:
    """Разбить записи на пакеты по ``size`` единиц."""
    return [entries[i : i + size] for i in range(0, len(entries), size)]


# ---------------------------------------------------------------- оркестрация
class RealSeeder(Seeder):
    """:class:`Seeder` для реальных единиц: те же шаги, другой набор файлов и исходников."""

    def __init__(
        self,
        config: SeedConfig,
        units: Sequence[RealUnit],
        *,
        package_units: int = PACKAGE_UNITS,
        package_timeout_s: float = PACKAGE_TIMEOUT_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._units = list(units)
        self._package_units = package_units
        self._package_timeout_s = package_timeout_s
        # Ключ идемпотентности пакета: повтор запроса в рамках запуска возвращает тот же пакет,
        # а новый запуск (после исправления файлов) обрабатывается заново.
        self._run_id = uuid.uuid4().hex[:12]
        self._audio: dict[str, Path] = {}
        self._token_at = 0.0

    def _present_import_files(self) -> list[str]:
        root = self._cfg.catalog_path()
        return [n for n in (REAL_FILMS_FILE, REAL_TRACKS_FILE) if (root / n).is_file()]

    def _keep_token(self) -> None:
        """Обновить admin-токен, если он старше ``TOKEN_REFRESH_S`` (засев дольше его жизни)."""
        if time.monotonic() - self._token_at > TOKEN_REFRESH_S:
            self.authenticate()
            self._token_at = time.monotonic()

    def authenticate(self) -> str:
        """Вход в админку с отметкой времени токена."""
        token = super().authenticate()
        self._token_at = time.monotonic()
        return token

    def _poll(self, check: Callable[[], bool], **kwargs: Any) -> None:
        """Опрос готовности; перед каждой проверкой токен обновляется при необходимости."""

        def checked() -> bool:
            self._keep_token()
            return check()

        super()._poll(checked, **kwargs)

    def _import_key(self, stem: str, file_id: str) -> str:
        """Ключ с отпечатком содержимого: файл растёт между запусками, старый ответ не годится."""
        digest = hashlib.sha256((self._cfg.catalog_path() / file_id).read_bytes()).hexdigest()
        return f"seed-import-{stem}-{digest[:12]}"

    def set_audio(self, audio: Mapping[str, Path]) -> None:
        """Подготовленные m4a (ключ единицы → файл)."""
        self._audio = dict(audio)

    def upload_real(self, items: Mapping[str, _ItemRef]) -> int:
        """Отправить недостающие исходники пакетами и дождаться наборов ассетов.

        Клиент только собирает zip с ``manifest.json`` и ждёт статус; приём файлов, проверки
        архива и нарезка — на стороне media-pipeline (``POST /admin/v1/packages``). Единицы с
        ошибками по файлам в ожидание наборов не попадают. Вернуть число единиц.
        """
        entries: list[PackageEntry] = []
        for unit in sorted(self._units, key=lambda u: u.key):
            ref = items.get(unit.key)
            if ref is None:
                continue
            self._keep_token()
            have = self._uploaded(ref.media_item_id)
            files = tuple(
                f
                for f in unit_files(unit, self._audio.get(unit.key))
                if (f.role, f.position or 1) not in have
            )
            if files:
                entries.append(PackageEntry(unit, ref.media_item_id, files))
        failed: set[str] = set()
        for batch in split_batches(entries, self._package_units):
            failed |= self._send_package(batch)
        waiting = 0
        for unit in sorted(self._units, key=lambda u: u.key):
            ref = items.get(unit.key)
            if ref is None or unit.key in failed or not unit_files(unit, self._audio.get(unit.key)):
                continue
            self._wait_assets(ref.media_item_id, unit.key)
            waiting += 1
        return waiting

    def _send_package(self, batch: Sequence[PackageEntry]) -> set[str]:
        """Собрать и отправить один пакет, дождаться статуса; вернуть ключи записей с ошибками."""
        with tempfile.TemporaryDirectory(prefix="gv-seed-package-") as tmp:
            archive = Path(tmp) / "package.zip"
            count = write_package(archive, batch)
            self._echo(
                f"seed-real: пакет — единиц {len(batch)}, файлов {count}, "
                f"{archive.stat().st_size // 1024} КБ"
            )
            self._keep_token()
            package_id = self._post_package(archive)
        return self._wait_package(package_id)

    def _post_package(self, archive: Path) -> str:
        """``POST /admin/v1/packages`` потоком; ключ идемпотентности: запуск и хэш архива."""
        hasher = hashlib.sha256()
        with archive.open("rb") as handle:
            while block := handle.read(PACKAGE_CHUNK):
                hasher.update(block)
        digest = hasher.hexdigest()
        size = archive.stat().st_size

        def chunks() -> Iterable[bytes]:
            with archive.open("rb") as handle:
                while block := handle.read(PACKAGE_CHUNK):
                    yield block

        resp = self._media.post(
            "/admin/v1/packages",
            params={"reason": REASON},
            headers={
                "Idempotency-Key": f"seed-package-{self._run_id}-{digest[:24]}",
                "Content-Type": "application/zip",
                "Content-Length": str(size),
            },
            content=chunks(),
            timeout=PACKAGE_UPLOAD_TIMEOUT_S,
        )
        self._check(resp, {202}, "отправка пакета")
        return str(resp.json()["package_id"])

    def _wait_package(self, package_id: str) -> set[str]:
        """Ждать завершения пакета; вывести ошибки по файлам, вернуть ключи записей с ошибками."""
        state: dict[str, Any] = {}

        def check() -> bool:
            self._keep_token()
            resp = self._media.get(f"/admin/v1/packages/{package_id}")
            self._check(resp, {200}, f"статус пакета {package_id}")
            state.update(resp.json())
            return str(state["status"]) in PACKAGE_TERMINAL

        self._poll(
            check,
            timeout_s=self._package_timeout_s,
            what=f"обработка пакета {package_id}",
            on_timeout=lambda: f"{state.get('done')}/{state.get('total')} файлов",
        )
        if state["status"] == "failed" and not state.get("done"):
            raise SeedError(
                f"пакет {package_id} отвергнут: {state.get('error_code')} — "
                f"{state.get('error_message') or 'все файлы с ошибками'}"
            )
        failed = {str(e["item_key"]) for e in state.get("errors", []) if e.get("item_key")}
        self._echo(
            f"seed-real: пакет {state['status']}: принято {state['done']} из {state['total']}, "
            f"без источника {state['missing_source']}, без лицензии {state['missing_license']}"
        )
        for error in list(state.get("errors", []))[:PACKAGE_ERRORS_SHOWN]:
            self._echo(f"  ! {error['item_key']} {error['path']}: {error['code']}")
        return failed

    # ------------------------------------------------------------ без синтетики
    def seed_standalone(
        self,
        plans: Sequence[ModePlan],
        specs: Sequence[CollectionSpec],
        items: Mapping[str, _ItemRef],
    ) -> list[SkippedMode]:
        """Шаги после загрузки реальных единиц на стенде без ``gv dev seed``.

        Режим без материала не получает набор и не роняет засев — он попадает в сводку.
        Порядок: пулы режимов → онлайн-шаблоны → сборники → лобби-сборники → наборы
        периода (когда реальные единицы уже в проекциях) → progression, события, social.
        """
        ready, skipped = self.plan_modes(plans, items)
        no_template = self._wait_templates([p for p in ready if p.online])
        skipped += no_template
        # Режим без шаблона дальше не ждём: иначе он дважды в сводке и ещё до таймаута на лобби.
        lost = {s.key for s in no_template}
        ready = [p for p in ready if p.key not in lost]
        skipped += self._seed_collections_soft(specs, items)
        skipped += self._wait_lobbies([p for p in ready if p.lobby_by_collection], specs, items)
        skipped += self._regenerate_soft([p for p in ready if p.period is not None])
        self.seed_progression()
        self.seed_events()
        self.seed_social()
        return skipped

    def plan_modes(
        self, plans: Sequence[ModePlan], items: Mapping[str, _ItemRef]
    ) -> tuple[list[ModePlan], list[SkippedMode]]:
        """Разделить режимы на имеющие материал и оставшиеся без него (с причиной)."""
        loaded = [u for u in self._units if u.key in items]
        ready: list[ModePlan] = []
        skipped: list[SkippedMode] = []
        for plan in plans:
            have = [u for u in loaded if plan.domain in (None, u.domain)]
            if len(have) < plan.min_playable:
                reason = (
                    f"нет единиц домена {plan.domain}"
                    if not have
                    else f"единиц {len(have)} из {plan.min_playable}"
                )
                skipped.append(SkippedMode(plan.key, reason))
                continue
            problem = self._playable_problem(plan)
            if problem is None:
                ready.append(plan)
            else:
                skipped.append(SkippedMode(plan.key, problem))
        return ready, skipped

    def _playable_problem(self, plan: ModePlan) -> str | None:
        """``None`` — в проекции game хватает играбельных единиц; иначе причина (по таймауту).

        Пока режим не опубликован (404) или пул не дорос, ждём: game публикует сид режима,
        когда под него готов пул.
        """
        state = {"note": ""}

        def enough() -> bool:
            try:
                count = len(self._playable_ids(plan.key))
            except SeedError as exc:
                state["note"] = str(exc)
                return False
            state["note"] = f"играбельных {count} из {plan.min_playable}"
            return count >= plan.min_playable

        timeout_s = min(self._cfg.assets_timeout_s, PLAYABLE_WAIT_S)
        self._echo(f"seed-real: ожидание пула {plan.key} (до {timeout_s:.0f} с)")
        try:
            self._poll(enough, timeout_s=timeout_s, what=f"пул {plan.key}")
        except SeedError:
            return state["note"]
        return None

    def _wait_templates(self, plans: Sequence[ModePlan]) -> list[SkippedMode]:
        try:
            self.wait_online_templates(plans)
        except SeedError as exc:
            return [SkippedMode(p.key, f"шаблон не опубликован: {exc}") for p in plans]
        return []

    def _seed_collections_soft(
        self, specs: Sequence[CollectionSpec], items: Mapping[str, _ItemRef]
    ) -> list[SkippedMode]:
        """Сборники по одному: недоступный сборник не мешает остальным."""
        skipped: list[SkippedMode] = []
        for spec in specs:
            try:
                self.seed_collections([spec], items)
            except SeedError as exc:
                skipped.append(SkippedMode(f"collection:{spec.key}", str(exc)))
        return skipped

    def _wait_lobbies(
        self,
        plans: Sequence[ModePlan],
        specs: Sequence[CollectionSpec],
        items: Mapping[str, _ItemRef],
    ) -> list[SkippedMode]:
        skipped: list[SkippedMode] = []
        for plan in plans:
            try:
                self.wait_online_collections(lobby_collections([plan], specs), items)
            except SeedError as exc:
                skipped.append(SkippedMode(plan.key, f"лобби по сборнику: {exc}"))
        return skipped

    def _regenerate_soft(self, plans: Sequence[ModePlan]) -> list[SkippedMode]:
        """Наборы периода по одному режиму: непригодный набор попадает в сводку, а не роняет."""
        skipped: list[SkippedMode] = []
        for plan in plans:
            try:
                self.regenerate_period_sets([plan])
            except SeedError as exc:
                skipped.append(SkippedMode(plan.key, f"набор периода: {exc}"))
        return skipped

    def _uploaded(self, media_item_id: str) -> set[tuple[str, int]]:
        resp = self._media.get("/admin/v1/sources", params={"media_item_id": media_item_id})
        self._check(resp, {200}, f"исходники единицы {media_item_id}")
        return {(str(s.get("kind")), int(s.get("position") or 1)) for s in resp.json()}


def default_ffmpeg() -> str:
    """ffmpeg из ``GV_FFMPEG`` или из PATH."""
    found = os.environ.get("GV_FFMPEG") or shutil.which("ffmpeg")
    if not found:
        raise SeedError("ffmpeg не найден: задайте --ffmpeg или GV_FFMPEG")
    return found


def run_seed_real(
    config: SeedConfig,
    *,
    real_dir: Path,
    ffmpeg: str,
    seedpack: Path | None = None,
    min_collection_size: int = MIN_COLLECTION_ITEMS,
    tracks_limit: int | None = None,
    package_units: int = PACKAGE_UNITS,
    without_synthetic: bool = False,
    echo: Callable[[str], None] = print,
) -> dict[str, int]:
    """Полный засев реальными медиа на живом стеке; вернуть итоговые числа.

    Args:
        without_synthetic: Стенд без ``gv dev seed``: после реальных данных выполняются
            базовые шаги (шаблоны, предметы, события, social), по одному реальному сборнику
            каждого домена получает «рекомендуемое», режимы без материала пропускаются и
            перечисляются в сводке.
    """
    units = build_units(real_dir, tracks_limit=tracks_limit)
    if not units:
        raise SeedError(f"в {real_dir} нет реальных единиц (tmdb/fma/jamendo)")
    films, tracks = import_rows(real_dir, units)
    out = config.catalog_path()
    write_jsonl(out / REAL_FILMS_FILE, films)
    write_jsonl(out / REAL_TRACKS_FILE, tracks)
    echo(f"seed-real: каталог — фильмов {len(films)}, треков {len(tracks)} → {out}")
    audio = prepare_audio(units, seedpack or real_dir / "seedpack", ffmpeg)
    echo(f"seed-real: аудио подготовлено: {len(audio)}")
    specs = music_collections(units, min_size=min_collection_size) + film_collections(units)
    if without_synthetic:
        specs = with_featured(specs)
    seeder = RealSeeder(config, units, package_units=package_units, echo=echo)
    skipped: list[SkippedMode] = []
    try:
        seeder.set_audio(audio)
        seeder.authenticate()
        plans = seeder.mode_plans()
        seeder.import_catalog()
        items = seeder.resolve_items({u.key for u in units})
        uploaded = seeder.upload_real(items)
        seeder.wait_profiles()
        if without_synthetic:
            skipped = seeder.seed_standalone(plans, specs, items)
        else:
            seeder.seed_collections(specs, items)
            seeder.regenerate_period_sets(plans)
    finally:
        seeder.close()
    if without_synthetic:
        featured = [s.key for s in specs if s.featured_rank is not None]
        echo(f"seed-real: рекомендуемые сборники: {featured or '—'}")
        for item in skipped:
            echo(f"seed-real: пропущено {item.key}: {item.reason}")
        echo(f"seed-real: пропущено режимов и сборников: {len(skipped)}")
    echo("seed-real: готово")
    return {
        "films": len(films),
        "tracks": len(tracks),
        "uploaded": uploaded,
        "collections": len(specs),
        "skipped": len(skipped),
    }
