"""Засев реальными медиа (``gv dev seed-real``) — только local.

Дополняет синтетический ``gv dev seed`` (ничего не удаляет): берёт выгрузки вне репозитория
(``--real-dir``, по умолчанию ``C:/gv-media``; ADR 0014 — бинарные файлы и контент третьих лиц
в git не хранятся):

* ``tmdb/catalog-films.jsonl`` — кино всех категорий (фильмы, сериалы, мультфильмы, аниме);
  файлы тайтла лежат в ``tmdb/<folder>/{poster,frame-1..6}.jpg``, ``folder`` — поле строки
  (``seeds/media/fetch_tmdb_popular.py``);
* ``deezer/clips/<id>.mp3`` + ``deezer/covers/<album_id>.jpg`` +
  ``deezer/catalog-tracks.jsonl`` — треки с обложками (``seeds/media/fetch_deezer.py``);
  обложка грузится ролью ``cover``; нет файла обложки — ``artwork`` ревила ``null``.

Шаги: перекодирование клипов → каталожные файлы импорта (``real-films.jsonl``,
``real-tracks.jsonl``) → импорт → исходники пакетами → ожидание наборов ассетов (по прогрессу,
а не по общему таймауту) → сборники по жанрам, популярности, категориям и десятилетиям → наборы
периода. Повторный запуск безопасен: ключи идемпотентности стабильны, уже загруженные
исходники пропускаются. Битые единицы (наборы ``failed``, отвергнутые пакеты, не
перекодировавшиеся клипы) не роняют засев: они пропускаются и перечисляются в сводке.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx

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
# Размеры сборников: минимум единиц в жанре/категории и размер сборника по умолчанию.
MIN_COLLECTION_ITEMS = 20
COLLECTION_SIZE = 50
# Через сколько секунд обновлять admin-токен (срок жизни короче засева).
TOKEN_REFRESH_S = 240.0
# Пакеты для media-pipeline: единиц в пакете, чтение файла при отправке, ожидание статуса.
PACKAGE_UNITS = 100
PACKAGE_CHUNK = 1024 * 1024
PACKAGE_UPLOAD_TIMEOUT_S = 600.0
PACKAGE_TIMEOUT_S = 3600.0
PACKAGE_ERRORS_SHOWN = 10
PACKAGE_TERMINAL = frozenset({"done", "partial", "failed"})
# Подряд отвергнутых пакетов, после которых засев останавливается (системный сбой, не битый файл).
PACKAGE_REJECTS_LIMIT = 3
# Ожидание наборов: таймаут только на отсутствие прогресса, а не на всё ожидание сразу.
STALL_TIMEOUT_S = 1800.0
# Пауза между проходами опроса: на 20 000 единиц один проход — тысячи запросов.
PASS_PAUSE_S = 10.0
# Как часто писать прогресс (единиц).
PROGRESS_EVERY = 500
# Сколько ключей битых единиц печатать в сводке; полный список — в файле рядом с seedpack.
BROKEN_SHOWN = 50
BROKEN_FILE = "broken-units.txt"
# Потоков перекодирования аудио по умолчанию.
FFMPEG_JOBS = min(8, os.cpu_count() or 4)
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
    "metrics",
    "countries",
)
_ALLOWED_METRICS = frozenset(
    {"popularity", "vote_average", "vote_count", "revenue_usd", "runtime_min", "duration_ms"}
)
# Контракт каталога (``FileLine``): роли персон и ключи внешних id. Контракт не расширяем:
# создатель сериала (``creator``) заменяется ближайшей допустимой ролью.
_ALLOWED_ROLES = frozenset({"artist", "director", "actor"})
_ROLE_FALLBACK: Mapping[str, str] = {"creator": "director"}
_ALLOWED_REFS = frozenset({"tmdb", "musicbrainz", "isrc", "spotify", "yandex_music"})
_MAX_PLOT = 5000
_MAX_TITLE = 300
_MAX_PERSON_NAME = 200
# Каталоги выгрузок треков (общий механизм «провайдер треков»): ``<real-dir>/<имя>/``.
TRACK_DIRS: tuple[str, ...] = ("deezer",)
# Происхождение исходников ``POST /admin/v1/sources`` и пометка лицензии.
ORIGIN_TRACK = "upload"
ORIGIN_FILM = "tmdb"
NOTE_FILM = "TMDB (local, ADR 0014); не для распространения"

# Названия жанровых сборников. Неизвестный жанр получает название «Жанр: <ключ>».
GENRE_TITLES: Mapping[str, str] = {
    "pop": "Поп",
    "hip-hop": "Хип-хоп",
    "rock": "Рок",
    "rnb": "R&B",
    "dance": "Танцевальная",
    "electronic": "Электроника",
    "alternative": "Альтернатива",
    "soundtrack": "Саундтреки",
    "latin": "Латино",
    "soul": "Соул и фанк",
    "world": "Музыка мира",
    "metal": "Метал",
    "jazz": "Джаз",
    "reggae": "Регги",
    "classical": "Классика",
    "folk": "Фолк",
    "blues": "Блюз",
    "country": "Кантри",
    "indie": "Инди",
    "punk": "Панк",
    "funk": "Фанк",
    "disco": "Диско",
    "ambient": "Эмбиент",
    "lounge": "Лаунж",
    "instrumental": "Инструментал",
    "experimental": "Эксперимент",
    "international": "Международная",
    "k-pop": "K-pop",
    "reggaeton": "Реггетон",
    "gospel": "Госпел",
    "opera": "Опера",
    "comedy": "Юмор",
}
# Написания жанра из разных источников → общий ключ.
GENRE_ALIASES: Mapping[str, str] = {"hiphop": "hip-hop", "hip hop": "hip-hop", "r&b": "rnb"}
MUSIC_COVERS = ("vinyl", "neon", "night", "sunset", "forest", "ocean")
FILM_COVERS = ("cinema", "sunset", "night", "popcorn")
MUSIC_MODE = "music.collection.intro"
FILM_MODE = "film.collection.frame"
# «Рекомендуемое» без синтетики: порядок по доменам и предпочитаемый сборник домена; топы
# категорий кино получают следующие порядки.
FEATURED_RANKS: Mapping[str, int] = {"film": 1, "music": 2}
FEATURED_KEYS: Mapping[str, str] = {"film": "real-film-top", "music": "real-music-top"}
FEATURED_CATEGORY_RANK = 3
_CATEGORY_TOP = re.compile(r"^real-film-.+-top$")
# Сколько ждать пул одного режима без синтетики без роста числа играбельных единиц, прежде чем
# пропустить режим (в сводку): иначе N режимов без материала держали бы засев N × таймаут.
PLAYABLE_STALL_S = 300.0

# Типы колбэков для тестов: ffmpeg и копирование не выполняются в unit-тестах.
Runner = Callable[[Sequence[str]], None]

_TRANSLIT = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p",
         "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
        strict=True,
    )
)  # fmt: skip


def slugify(text: str) -> str:
    """ASCII-ключ из текста: кириллица транслитерируется, остальное — через ``-``."""
    latin = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    return re.sub(r"[^a-z0-9]+", "-", latin).strip("-")


@dataclass(frozen=True, slots=True)
class RealUnit:
    """Единица реального засева: ключ, домен и её файлы на диске.

    Attributes:
        key: Ключ строки каталога (``film:tmdb-1``, ``film:tmdb-tv-2``, ``track:deezer-3``).
        domain: ``film``/``music``.
        title: Название (для сборников и отчёта).
        year: Год, если известен.
        genres: Жанры.
        rating: ``vote_average`` фильма; у трека — ``None``.
        popularity: Популярность: ``popularity`` TMDB у кино, прослушивания (``rank``) у трека.
        license: Лицензия трека (в ``license_note`` загрузки); пусто — не передаётся.
        source_url: Страница трека у источника (в ``source_url`` загрузки); пусто — не передаётся.
        category: Категория кино (``Фильмы``, ``Сериалы``, ``Мультфильмы``, ``Аниме``).
        media_type: ``movie`` или ``tv`` (кино); у трека пусто.
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
    popularity: float | None = None
    license: str = ""
    source_url: str = ""
    category: str = ""
    media_type: str = ""
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


def _persons(raw: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Персоны для импорта: роли приводятся к допустимым контрактом, прочие отбрасываются."""
    result: list[dict[str, Any]] = []
    for person in raw:
        role = _ROLE_FALLBACK.get(str(person.get("role")), str(person.get("role")))
        name = _clean(str(person.get("name") or ""))[:_MAX_PERSON_NAME]
        if role in _ALLOWED_ROLES and name:  # пустое имя каталог отвергает всей строкой
            result.append({**person, "role": role, "name": name})
    return result


def catalog_row(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Строка выгрузки → строка импорта каталога.

    Оставляет только поля импорта (неизвестные catalog считает ошибкой строки), чистит
    названия от HTML-сущностей, переносит ``listens`` в ``popularity`` и ``overview`` в
    ``clue_texts.plot_raw`` (текст загадки «описание»; маскируется самим catalog). Роль
    ``creator`` (сериалы) становится ``director``; внешний id сериала получает префикс
    ``tv-``, чтобы не совпасть с id фильма (catalog объединяет единицы по ``external_refs``).
    """
    row: dict[str, Any] = {
        name: raw[name] for name in _IMPORT_FIELDS if raw.get(name) not in (None, [], {})
    }
    row["title"] = _clean(str(raw["title"]))[:_MAX_TITLE]
    if row.get("original_title"):
        row["original_title"] = _clean(str(row["original_title"]))[:_MAX_TITLE]
    persons = _persons(raw.get("persons") or [])
    if persons:
        row["persons"] = persons
    else:
        row.pop("persons", None)
    refs = {
        str(k): str(v) for k, v in (raw.get("external_refs") or {}).items()
        if k in _ALLOWED_REFS and v
    }  # fmt: skip
    if refs.get("tmdb") and raw.get("media_type") == "tv":
        refs["tmdb"] = f"tv-{refs['tmdb']}"
    if refs:
        row["external_refs"] = refs
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


def normalize_genre(name: str) -> str:
    """Жанр в нижнем регистре и общем написании (``hiphop`` → ``hip-hop``)."""
    key = " ".join(name.lower().split())
    return GENRE_ALIASES.get(key, key)


def load_track_rows(real_dir: Path, dirs: Sequence[str] = TRACK_DIRS) -> list[dict[str, Any]]:
    """Треки с клипами на диске из выгрузок ``<real-dir>/<dir>/catalog-tracks.jsonl``.

    ``audio`` и ``cover`` в возвращаемых строках — абсолютные пути. Строки без клипа
    отбрасываются (выгрузка может быть неполной); трек без файла обложки идёт без неё
    (``cover`` — ``None``, ``artwork`` будет ``null``). Жанры приводятся к общему написанию.
    """
    rows: list[dict[str, Any]] = []
    for name in dirs:
        folder = real_dir / name
        catalog = folder / "catalog-tracks.jsonl"
        if not catalog.is_file():
            continue
        for row in read_jsonl(catalog):
            audio = str(row.get("audio") or "")
            clip = folder / "clips" / audio
            if not audio or not clip.is_file() or clip.stat().st_size == 0:
                continue
            cover_name = str(row.get("cover") or "")
            cover = folder / "covers" / cover_name
            has_cover = bool(cover_name) and cover.is_file() and cover.stat().st_size > 0
            rows.append(
                {
                    **row,
                    "audio": str(clip),
                    "cover": str(cover) if has_cover else None,
                    "genres": [normalize_genre(str(g)) for g in row.get("genres") or ()],
                }
            )
    return rows


def load_film_rows(tmdb_dir: Path) -> list[dict[str, Any]]:
    """Кино выгрузки (``catalog-films.jsonl``), у которого на диске есть постер и все кадры."""
    path = tmdb_dir / "catalog-films.jsonl"
    if not path.is_file():
        return []
    return [r for r in read_jsonl(path) if _film_files(tmdb_dir, r) is not None]


def _film_folder(tmdb_dir: Path, row: Mapping[str, Any]) -> Path | None:
    """Папка тайтла: ``folder`` строки (``Аниме/movie-129``) или ``tmdb/<id>`` в старой выгрузке."""
    rel = str(row.get("folder") or "")
    if not rel:
        return tmdb_dir / str(row["key"]).rsplit("-", 1)[-1]
    path = Path(rel)
    if path.is_absolute() or ".." in path.parts:
        return None
    return tmdb_dir.joinpath(*path.parts)


def _film_files(tmdb_dir: Path, row: Mapping[str, Any]) -> tuple[Path, tuple[Path, ...]] | None:
    """Постер и кадры тайтла; неполный набор — ``None``."""
    folder = _film_folder(tmdb_dir, row)
    if folder is None:
        return None
    poster = folder / Path(str(row.get("poster") or "poster.jpg")).name
    frames = tuple(
        folder / Path(str(name)).name for name in (row.get("frames") or ())[:FRAMES_PER_FILM]
    )
    if (
        not poster.is_file()
        or len(frames) < FRAMES_PER_FILM
        or not all(f.is_file() for f in frames)
    ):
        return None
    return poster, frames


def _media_type(row: Mapping[str, Any]) -> str:
    return str(row.get("media_type") or ("tv" if "tmdb-tv-" in str(row["key"]) else "movie"))


def build_units(real_dir: Path, *, tracks_limit: int | None = None) -> list[RealUnit]:
    """Все единицы засева с файлами; порядок — кино по порядку каталога, затем треки."""
    tmdb_dir = real_dir / "tmdb"
    units: list[RealUnit] = []
    for row in load_film_rows(tmdb_dir):
        poster, frames = _film_files(tmdb_dir, row) or (None, ())
        metrics = row.get("metrics") or {}
        units.append(
            RealUnit(
                key=str(row["key"]),
                domain="film",
                title=_clean(str(row["title"])),
                year=row.get("year"),
                genres=tuple(row.get("genres") or ()),
                rating=float(metrics.get("vote_average") or 0),
                popularity=float(metrics.get("popularity") or 0),
                category=str(row.get("category") or ""),
                media_type=_media_type(row),
                poster=poster,
                frames=frames,
            )
        )
    units.extend(
        RealUnit(
            key=str(row["key"]),
            domain="music",
            title=_clean(str(row["title"])),
            year=row.get("year"),
            genres=tuple(row.get("genres") or ()),
            popularity=float((row.get("metrics") or {}).get("listens") or 0),
            license=str(row.get("license") or ""),
            source_url=str(row.get("source_url") or ""),
            audio=Path(row["audio"]),
            cover=Path(row["cover"]) if row.get("cover") else None,
        )
        for row in load_track_rows(real_dir)[:tracks_limit]
    )
    return units


def import_rows(
    real_dir: Path, units: Iterable[RealUnit]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Строки импорта (кино, треки) только для единиц ``units``."""
    keep = {u.key for u in units}
    films = [catalog_row(r) for r in load_film_rows(real_dir / "tmdb") if r["key"] in keep]
    tracks = [catalog_row(r) for r in load_track_rows(real_dir) if r["key"] in keep]
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
    units: Iterable[RealUnit],
    seedpack: Path,
    ffmpeg: str,
    run: Runner = _run,
    *,
    jobs: int = 1,
    echo: Callable[[str], None] = lambda _msg: None,
) -> tuple[dict[str, Path], dict[str, str]]:
    """Перекодировать клипы треков в ``jobs`` потоков; готовые файлы не трогает.

    Кэш — сам файл ``audio/<ключ>.m4a``: кодирование идёт во временный ``*.part.m4a`` и
    переименовывается по завершении, поэтому прерванный запуск не оставляет «готовый» битый
    файл. Клип, который не перекодировался, не роняет засев.

    Returns:
        Ключ единицы → m4a и ключ единицы → причина (для клипов, которые не удалось перекодировать).
    """
    ready: dict[str, Path] = {}
    todo: list[tuple[RealUnit, Path]] = []
    for unit in units:
        if unit.audio is None:
            continue
        dst = audio_target(seedpack, unit.key)
        if dst.is_file() and dst.stat().st_size > 0:
            ready[unit.key] = dst
        else:
            todo.append((unit, dst))

    def encode(job: tuple[RealUnit, Path]) -> tuple[str, Path | None, str]:
        unit, dst = job
        tmp = dst.with_name(f"{dst.stem}.part{dst.suffix}")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            run(ffmpeg_command(ffmpeg, unit.audio or Path(), tmp))
            if not tmp.is_file() or tmp.stat().st_size == 0:
                raise OSError("ffmpeg не создал файл")  # noqa: TRY301 — единый путь ошибки
            tmp.replace(dst)
        except (subprocess.CalledProcessError, OSError) as exc:
            tmp.unlink(missing_ok=True)
            return unit.key, None, f"перекодирование аудио: {exc}"
        return unit.key, dst, ""

    failed: dict[str, str] = {}
    if todo:
        echo(f"seed-real: перекодирование аудио — {len(todo)} клипов в {max(1, jobs)} потоков")
    pool = ThreadPoolExecutor(max_workers=max(1, jobs))
    try:
        for number, (key, encoded, error) in enumerate(pool.map(encode, todo), start=1):
            if encoded is None:
                failed[key] = error
            else:
                ready[key] = encoded
            if number % PROGRESS_EVERY == 0 or number == len(todo):
                echo(f"seed-real: аудио {number}/{len(todo)}")
    except BaseException:
        # Ctrl+C: не кодировать оставшуюся очередь (до десятков тысяч клипов).
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return ready, failed


# ---------------------------------------------------------------- сборники
def _items(units: Iterable[RealUnit]) -> tuple[CollectionItemSpec, ...]:
    return tuple(CollectionItemSpec(seed_key=u.key) for u in units)


def _by_popularity(units: Iterable[RealUnit]) -> list[RealUnit]:
    """Самые популярные первыми; при равенстве — по рейтингу, затем по ключу."""
    return sorted(units, key=lambda u: (-(u.popularity or 0), -(u.rating or 0), u.key))


def music_collections(
    units: Iterable[RealUnit],
    *,
    min_size: int = MIN_COLLECTION_ITEMS,
    max_size: int = COLLECTION_SIZE,
) -> list[CollectionSpec]:
    """Музыкальные сборники: общий «Топ треков» и по сборнику на каждый жанр треков.

    Жанр берётся по первому жанру трека; жанры с числом треков меньше ``min_size`` пропускаются.
    Внутри сборника — по популярности (прослушиваниям). Жанры идут от самых крупных.
    """
    tracks = [u for u in units if u.domain == "music"]
    specs: list[CollectionSpec] = []
    if len(tracks) >= min_size:
        specs.append(
            CollectionSpec(
                key=FEATURED_KEYS["music"],
                title="Топ треков",
                domain="music",
                default_mode_key=MUSIC_MODE,
                items=_items(_by_popularity(tracks)[:max_size]),
                description="Угадайте трек по вступлению: самые популярные треки.",
                cover=MUSIC_COVERS[0],
            )
        )
    # Группировка по ключу сборника, а не по сырому жанру: «k-pop» и «k pop» дают один ключ.
    by_genre: dict[str, list[RealUnit]] = {}
    names: dict[str, str] = {}
    for unit in tracks:
        if unit.genres and (slug := slugify(unit.genres[0])):
            by_genre.setdefault(slug, []).append(unit)
            names.setdefault(slug, unit.genres[0])
    fitting = [(g, u) for g, u in by_genre.items() if len(u) >= min_size]
    fitting.sort(key=lambda gu: (-len(gu[1]), gu[0]))
    for slug, members in fitting:
        genre = names[slug]
        title = GENRE_TITLES.get(genre, f"Жанр: {genre}")
        specs.append(
            CollectionSpec(
                key=f"real-music-{slug}",
                title=title,
                domain="music",
                default_mode_key=MUSIC_MODE,
                items=_items(_by_popularity(members)[:max_size]),
                description=f"Угадайте трек по вступлению: {title}.",
                cover=MUSIC_COVERS[len(specs) % len(MUSIC_COVERS)],
            )
        )
    return specs


def film_collections(
    units: Iterable[RealUnit],
    *,
    min_size: int = MIN_COLLECTION_ITEMS,
    max_size: int = COLLECTION_SIZE,
) -> list[CollectionSpec]:
    """Киношные сборники по популярности: общий топ, топ и жанры каждой категории, десятилетия.

    Ключи — ASCII (``real-film-top``, ``real-film-anime-top``, ``real-film-filmy-drama``,
    ``real-film-1990s``). Сборник меньше ``min_size`` единиц пропускается. Категории и жанры
    берутся из данных, десятилетия — из лет выпуска.
    """
    films = [u for u in units if u.domain == "film"]
    specs: list[CollectionSpec] = []

    def add(key: str, title: str, description: str, members: Sequence[RealUnit]) -> None:
        if len(members) < min_size:
            return
        specs.append(
            CollectionSpec(
                key=key,
                title=title,
                domain="film",
                default_mode_key=FILM_MODE,
                items=_items(_by_popularity(members)[:max_size]),
                description=description,
                cover=FILM_COVERS[len(specs) % len(FILM_COVERS)],
            )
        )

    add(FEATURED_KEYS["film"], "Топ по популярности", "Самое популярное кино по кадрам.", films)
    sizes = Counter(u.category for u in films if u.category)
    for category in sorted(sizes, key=lambda c: (-sizes[c], c)):
        prefix = slugify(category)
        if not prefix:
            continue
        members = [u for u in films if u.category == category]
        add(
            f"real-film-{prefix}-top",
            f"{category}: топ",
            f"Самое популярное: {category.lower()}.",
            members,
        )
        genres = Counter(g for u in members for g in set(u.genres))
        for genre in sorted(genres, key=lambda g: (-genres[g], g)):
            if slugify(genre):
                add(
                    f"real-film-{prefix}-{slugify(genre)}",
                    f"{category}: {genre}",
                    f"{category}, жанр «{genre}»: угадайте по кадрам.",
                    [u for u in members if genre in u.genres],
                )
    for decade in sorted({u.year // 10 * 10 for u in films if u.year}):
        add(
            f"real-film-{decade}s",
            f"Кино {decade}-х",
            f"Кино {decade}–{decade + 9} годов по кадрам.",
            [u for u in films if u.year and u.year // 10 * 10 == decade],
        )
    return specs


def build_collections(
    units: Sequence[RealUnit],
    *,
    min_size: int = MIN_COLLECTION_ITEMS,
    max_size: int = COLLECTION_SIZE,
) -> list[CollectionSpec]:
    """Все сборники реальных единиц: музыка, затем кино."""
    return music_collections(units, min_size=min_size, max_size=max_size) + film_collections(
        units, min_size=min_size, max_size=max_size
    )


def with_featured(specs: Sequence[CollectionSpec]) -> list[CollectionSpec]:
    """Отметить «рекомендуемым» лучший сборник каждого домена (без синтетики отметить некого).

    Кино — «Топ по популярности», музыка — «Топ треков», если они есть, иначе первый сборник
    домена. Топы категорий кино получают следующие порядки. Сборник с уже заданным
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
            preferred = [s for s in of_domain if s.key == FEATURED_KEYS[domain]]
            chosen[domain] = (preferred or of_domain)[0].key
    category_tops = [
        s.key
        for s in specs
        if s.domain == "film" and _CATEGORY_TOP.match(s.key) and s.key not in chosen.values()
    ]
    category_ranks = {k: FEATURED_CATEGORY_RANK + n for n, k in enumerate(category_tops)}

    def rank_of(spec: CollectionSpec) -> int | None:
        if spec.featured_rank is not None:
            return spec.featured_rank
        if chosen.get(spec.domain) == spec.key:
            return FEATURED_RANKS[spec.domain]
        return category_ranks.get(spec.key)

    return [
        replace(s, featured_rank=rank) if (rank := rank_of(s)) != s.featured_rank else s
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
        # Источник — в ``source_url``, ``origin`` остаётся ``upload``: контракт манифеста
        # значений провайдеров не знает и не расширяется. Лицензия необязательна (ADR 0072).
        result = {"origin": ORIGIN_TRACK}
        if unit.license:
            result["license_note"] = f"{unit.license} (local, ADR 0014)"
        if unit.source_url:
            result["source_url"] = unit.source_url
        return result
    tmdb_id = unit.key.rsplit("-", 1)[-1]
    media = unit.media_type or ("tv" if "tmdb-tv-" in unit.key else "movie")
    return {
        "origin": ORIGIN_FILM,
        "source_url": f"https://www.themoviedb.org/{media}/{tmdb_id}",
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
    """:class:`Seeder` для реальных единиц: те же шаги, другой набор файлов и исходников.

    Attributes:
        broken: Битые единицы запуска: ключ → причина. Они не ждутся, не попадают в сборники
            и перечисляются в сводке; повторный запуск пробует их снова.
    """

    def __init__(
        self,
        config: SeedConfig,
        units: Sequence[RealUnit],
        *,
        package_units: int = PACKAGE_UNITS,
        package_timeout_s: float = PACKAGE_TIMEOUT_S,
        stall_timeout_s: float = STALL_TIMEOUT_S,
        pass_pause_s: float = PASS_PAUSE_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(config, **kwargs)
        self._units = list(units)
        self._package_units = package_units
        self._package_timeout_s = package_timeout_s
        self._stall_timeout_s = stall_timeout_s
        self._pass_pause_s = pass_pause_s
        # Ключ идемпотентности пакета: повтор запроса в рамках запуска возвращает тот же пакет,
        # а новый запуск (после исправления файлов) обрабатывается заново.
        self._run_id = uuid.uuid4().hex[:12]
        self._audio: dict[str, Path] = {}
        self._token_at = 0.0
        self.broken: dict[str, str] = {}

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

    def _wait_progress(
        self,
        step: Callable[[], tuple[bool, float]],
        *,
        what: str,
        details: Callable[[], str],
        stall_s: float | None = None,
    ) -> None:
        """Повторять ``step`` до готовности; таймаут — только если прогресса нет ``stall_s``.

        ``step`` возвращает (готово, мера прогресса): рост меры сбрасывает отсчёт. Общего
        предела на всё ожидание нет — большой засев может идти часами, пока он движется.
        """
        limit = self._stall_timeout_s if stall_s is None else stall_s
        best: float | None = None
        moved_at = time.monotonic()
        while True:
            done, marker = step()
            if done:
                return
            now = time.monotonic()
            if best is None or marker > best:
                best, moved_at = marker, now
            elif now - moved_at >= limit:
                raise SeedError(
                    f"таймаут ожидания ({what}): нет прогресса {limit:.0f} с — {details()}"
                )
            time.sleep(self._pass_pause_s)

    def _import_key(self, stem: str, file_id: str) -> str:
        """Ключ с отпечатком содержимого: файл растёт между запусками, старый ответ не годится."""
        digest = hashlib.sha256((self._cfg.catalog_path() / file_id).read_bytes()).hexdigest()
        return f"seed-import-{stem}-{digest[:12]}"

    def set_audio(self, audio: Mapping[str, Path]) -> None:
        """Подготовленные m4a (ключ единицы → файл)."""
        self._audio = dict(audio)

    def drop_broken(self) -> None:
        """Убрать битые единицы из списка засева (сборники и пулы строятся без них)."""
        self._units = [u for u in self._units if u.key not in self.broken]

    def upload_real(self, items: Mapping[str, _ItemRef]) -> int:
        """Отправить недостающие исходники пакетами и дождаться наборов ассетов.

        Клиент только собирает zip с ``manifest.json`` и ждёт статус; приём файлов, проверки
        архива и нарезка — на стороне media-pipeline (``POST /admin/v1/packages``). Единицы с
        ошибками (файлы пакета, наборы ``failed``) не роняют засев: они попадают в
        :attr:`broken`. Вернуть число единиц с готовыми наборами.
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
        batches = split_batches(entries, self._package_units)
        rejects = 0
        for number, batch in enumerate(batches, start=1):
            failed, rejected = self._send_package_soft(batch, f"{number}/{len(batches)}")
            self.broken.update(failed)
            rejects = rejects + 1 if rejected else 0
            if rejects >= PACKAGE_REJECTS_LIMIT:
                raise SeedError(
                    f"отвергнуто {rejects} пакета подряд — похоже на системный сбой, "
                    f"а не битые файлы: {next(iter(failed.values()))}"
                )
        pending = {
            u.key: items[u.key].media_item_id
            for u in sorted(self._units, key=lambda u: u.key)
            if u.key in items and u.key not in self.broken and unit_files(u, self._audio.get(u.key))
        }
        return self._wait_unit_assets(pending)

    def _send_package_soft(
        self, batch: Sequence[PackageEntry], label: str
    ) -> tuple[dict[str, str], bool]:
        """Отправить пакет; ошибки превращаются в битые единицы.

        Returns:
            Битые единицы (ключ → причина) и признак «пакет отвергнут целиком».
        """
        try:
            failed = self._send_package(batch, label)
        except (SeedError, httpx.TransportError) as exc:
            reason = f"пакет {label} не принят: {exc}"
            self._echo(f"seed-real: {reason}")
            return {e.unit.key: reason for e in batch}, True
        return dict.fromkeys(failed, "ошибка файла в пакете"), False

    def _send_package(self, batch: Sequence[PackageEntry], label: str = "") -> set[str]:
        """Собрать и отправить один пакет, дождаться статуса; вернуть ключи записей с ошибками."""
        with tempfile.TemporaryDirectory(prefix="gv-seed-package-") as tmp:
            archive = Path(tmp) / "package.zip"
            count = write_package(archive, batch)
            self._echo(
                f"seed-real: пакет {label or '—'} — единиц {len(batch)}, файлов {count}, "
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
            try:
                resp = self._media.get(f"/admin/v1/packages/{package_id}")
            except httpx.TransportError:
                return False  # media-pipeline перезапускается: пакет дообработается, спросим снова
            if resp.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
                return False
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

    # ------------------------------------------------------------ наборы ассетов
    def _unit_assets_state(self, media_item_id: str, seed_key: str) -> str | None:
        """``None`` — ещё в работе; пустая строка — все наборы готовы; иначе причина поломки."""
        self._keep_token()
        try:
            resp = self._media.get("/admin/v1/asset-sets", params={"media_item_id": media_item_id})
        except httpx.TransportError:
            return None  # media-pipeline перезапускается: спросим на следующем проходе
        if resp.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            return None
        self._check(resp, {200}, f"наборы ассетов {seed_key}")
        sets = resp.json()
        failed = sorted(
            f"{s.get('clue_type')}/{s.get('profile_key')}"
            + (f" ({code})" if (code := s.get("error_code") or s.get("error")) else "")
            for s in sets
            if s["status"] == "failed"
        )
        if failed:
            return f"наборы ассетов в статусе failed: {failed}"
        # ``disabled`` (набор отключён администратором) готовности единицы не мешает.
        active = [s for s in sets if s["status"] != "disabled"]
        return "" if active and all(s["status"] == "ready" for s in active) else None

    def _wait_unit_assets(self, pending: dict[str, str]) -> int:
        """Ждать наборы единиц проходами; ``failed`` — в :attr:`broken`. Вернуть число готовых.

        Таймаут — только на отсутствие прогресса (ни одной новой готовой или проваленной
        единицы за ``stall_timeout_s``), а не на весь список сразу.
        """
        total = len(pending)
        stats = {"ready": 0, "failed": 0}
        self._echo(f"seed-real: ожидание наборов ассетов: {total} единиц")

        def report() -> None:
            self._echo(
                f"seed-real: наборы готовы {stats['ready']}/{total}, "
                f"проваленных {stats['failed']}, в работе {len(pending)}"
            )

        def step() -> tuple[bool, float]:
            for key, item_id in list(pending.items()):
                verdict = self._unit_assets_state(item_id, key)
                if verdict is None:
                    continue
                del pending[key]
                if verdict:
                    self.broken[key] = verdict
                    stats["failed"] += 1
                else:
                    stats["ready"] += 1
                if (stats["ready"] + stats["failed"]) % PROGRESS_EVERY == 0:
                    report()
            return not pending, float(total - len(pending))

        self._wait_progress(
            step,
            what="наборы ассетов единиц",
            details=lambda: f"в работе {len(pending)}: {sorted(pending)[:5]}",
        )
        report()
        return stats["ready"]

    def wait_profiles(self) -> None:
        """Статические профили без наборов в работе; ``failed`` не роняют засев.

        Проваленные наборы единиц текущего списка уже учтены в :attr:`broken`; наборы единиц,
        которых в списке засева нет (прошлые запуски), игнорируются.
        """
        self._echo("seed-real: ожидание профилей media-pipeline")
        state: dict[str, Any] = {"busy": [], "failed": 0}

        def step() -> tuple[bool, float]:
            self._keep_token()
            resp = self._media.get("/admin/v1/profiles")
            self._check(resp, {200}, "профили media-pipeline")
            static = [p for p in resp.json() if p.get("origin") == "static"]
            state["failed"] = sum(int(p["sets"]["failed"]) for p in static)
            busy = [p for p in static if p["sets"]["pending"]]
            state["busy"] = sorted(f"{p['clue_type']}/{p.get('profile_key')}" for p in busy)
            return not busy, -float(sum(int(p["sets"]["pending"]) for p in busy))

        self._wait_progress(
            step,
            what="наборы статических профилей",
            details=lambda: f"в работе: {state['busy']}",
        )
        if state["failed"]:
            self._echo(
                f"seed-real: наборов профилей в статусе failed: {state['failed']} "
                "(единицы вне списка засева игнорируются)"
            )

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
        """``None`` — в проекции game хватает играбельных единиц; иначе причина (нет прогресса).

        Пока режим не опубликован (404) или пул не дорос, ждём: game публикует сид режима,
        когда под него готов пул, а на тысячах единиц пул растёт долго. Сдаёмся, только если
        число играбельных единиц не растёт ``PLAYABLE_STALL_S``.
        """
        state = {"note": ""}

        def step() -> tuple[bool, float]:
            self._keep_token()
            try:
                count = len(self._playable_ids(plan.key))
            except SeedError as exc:
                state["note"] = str(exc)
                return False, -1.0
            state["note"] = f"играбельных {count} из {plan.min_playable}"
            return count >= plan.min_playable, float(count)

        stall_s = min(self._stall_timeout_s, PLAYABLE_STALL_S)
        self._echo(f"seed-real: ожидание пула {plan.key} (до {stall_s:.0f} с без прогресса)")
        try:
            self._wait_progress(
                step, what=f"пул {plan.key}", details=lambda: state["note"], stall_s=stall_s
            )
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
        for number, spec in enumerate(specs, start=1):
            try:
                self.seed_collections([spec], items)
            except SeedError as exc:
                skipped.append(SkippedMode(f"collection:{spec.key}", str(exc)))
            if number % 10 == 0 or number == len(specs):
                self._echo(f"seed-real: сборники {number}/{len(specs)}")
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


def report_broken(broken: Mapping[str, str], seedpack: Path, echo: Callable[[str], None]) -> None:
    """Сводка битых единиц: первые ``BROKEN_SHOWN`` в лог, полный список — в файл seedpack."""
    if not broken:
        return
    seedpack.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}\t{reason}" for key, reason in sorted(broken.items())]
    (seedpack / BROKEN_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    echo(f"seed-real: пропущено битых единиц: {len(broken)} (список: {seedpack / BROKEN_FILE})")
    for line in lines[:BROKEN_SHOWN]:
        echo(f"  ! {line}")
    if len(lines) > BROKEN_SHOWN:
        echo(f"  … и ещё {len(lines) - BROKEN_SHOWN}")


def run_seed_real(
    config: SeedConfig,
    *,
    real_dir: Path,
    ffmpeg: str,
    seedpack: Path | None = None,
    min_collection_size: int = MIN_COLLECTION_ITEMS,
    collection_size: int = COLLECTION_SIZE,
    tracks_limit: int | None = None,
    package_units: int = PACKAGE_UNITS,
    ffmpeg_jobs: int = FFMPEG_JOBS,
    stall_timeout_s: float = STALL_TIMEOUT_S,
    without_synthetic: bool = False,
    echo: Callable[[str], None] = print,
) -> dict[str, int]:
    """Полный засев реальными медиа на живом стеке; вернуть итоговые числа.

    Args:
        min_collection_size: Минимум единиц для сборника жанра/категории/десятилетия.
        collection_size: Размер сборника (самые популярные).
        package_units: Единиц в одном пакете media-pipeline.
        ffmpeg_jobs: Потоков перекодирования клипов.
        stall_timeout_s: Предел ожидания наборов ассетов без единого нового готового набора.
        without_synthetic: Стенд без ``gv dev seed``: после реальных данных выполняются
            базовые шаги (шаблоны, предметы, события, social), лучший сборник каждого домена
            получает «рекомендуемое», режимы без материала пропускаются и перечисляются в
            сводке.
    """
    pack = seedpack or real_dir / "seedpack"
    units = build_units(real_dir, tracks_limit=tracks_limit)
    if not units:
        raise SeedError(f"в {real_dir} нет реальных единиц (tmdb/deezer)")
    audio, audio_failed = prepare_audio(units, pack, ffmpeg, jobs=ffmpeg_jobs, echo=echo)
    echo(f"seed-real: аудио подготовлено: {len(audio)}, с ошибкой: {len(audio_failed)}")
    units = [u for u in units if u.key not in audio_failed]
    films, tracks = import_rows(real_dir, units)
    out = config.catalog_path()
    write_jsonl(out / REAL_FILMS_FILE, films)
    write_jsonl(out / REAL_TRACKS_FILE, tracks)
    echo(f"seed-real: каталог — кино {len(films)}, треков {len(tracks)} → {out}")
    seeder = RealSeeder(
        config,
        units,
        package_units=package_units,
        stall_timeout_s=stall_timeout_s,
        echo=echo,
    )
    skipped: list[SkippedMode] = []
    specs: list[CollectionSpec] = []
    uploaded = 0
    try:
        seeder.set_audio(audio)
        seeder.authenticate()
        plans = seeder.mode_plans()
        seeder.import_catalog()
        items = seeder.resolve_items({u.key for u in units})
        uploaded = seeder.upload_real(items)
        seeder.drop_broken()
        broken = {**audio_failed, **seeder.broken}
        healthy = [u for u in units if u.key not in broken]
        items = {k: v for k, v in items.items() if k not in broken}
        specs = build_collections(healthy, min_size=min_collection_size, max_size=collection_size)
        if without_synthetic:
            specs = with_featured(specs)
        seeder.wait_profiles()
        if without_synthetic:
            skipped = seeder.seed_standalone(plans, specs, items)
        else:
            seeder.seed_collections(specs, items)
            seeder.regenerate_period_sets(plans)
    finally:
        seeder.close()
    broken = {**audio_failed, **seeder.broken}
    report_broken(broken, pack, echo)
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
        "broken": len(broken),
    }
