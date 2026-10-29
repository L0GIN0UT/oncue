"""Выгрузка треков Jamendo (Creative Commons) для локального стенда: клип 30 с + обложка альбома.

Только для local/ci: файлы лежат вне репозитория (по умолчанию `C:/gv-media/jamendo`, каталог
задаётся `GV_REAL_MEDIA_DIR`), в git не попадают (ADR 0014). Ключ API — `JAMENDO_CLIENT_ID`
в окружении (или в `<root>/.keys.env`), в логи не пишется.

    uv run python seeds/media/fetch_jamendo.py --count 400

Результат: `clips/<id>.mp3`, `covers/<album_id>.jpg`, `catalog-tracks.jsonl` (формат как у
`fetch_fma.py`, плюс `source_url`, `origin`, `cover`). Полные треки не сохраняются: исходник
нарезается ffmpeg и удаляется. Повторный запуск не качает готовое заново.

Лицензии (`license_ccurl`) сохраняются как есть и НЕ фильтруются: часть треков под NC/ND —
для локального стенда это допустимо, для публичного релиза условия нужно проверять.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.jamendo.com/v3.0/tracks/"
ROOT = Path(os.environ.get("GV_REAL_MEDIA_DIR", "C:/gv-media"))
DEFAULT_FFMPEG = "C:/Users/ivanl/AppData/Local/ffmpeg/ffmpeg-9.0.2-essentials_build/bin/ffmpeg.exe"
GENRES = (
    "rock",
    "pop",
    "electronic",
    "hiphop",
    "jazz",
    "classical",
    "metal",
    "folk",
    "reggae",
    "blues",
    "ambient",
    "lounge",
)
CLIP_S = 30
LONG_TRACK_S = 60  # от этой длины клип берём с 30-й секунды, у коротких — с середины
MAX_TITLE_LEN = 60
PAGE_SIZE = 50
USER_AGENT = "guessverse-seed/1.0"


def clip_start(duration_s: int) -> int:
    """Секунда, с которой режется клип: 30-я у длинных треков, середина у коротких."""
    if duration_s >= LONG_TRACK_S:
        return CLIP_S
    return max(0, min(duration_s // 2, duration_s - CLIP_S))


def parse_track(raw: dict[str, Any], genre: str) -> dict[str, Any] | None:
    """Приводит запись API к внутреннему виду или `None`, если трек не годится.

    Требования: разрешено скачивание, есть название (< 60 символов), исполнитель, обложка,
    жанр и длительность не короче клипа.
    """
    if str(raw.get("audiodownload_allowed")).lower() != "true" or not raw.get("audiodownload"):
        return None
    title = str(raw.get("name") or "").strip()
    artist = str(raw.get("artist_name") or "").strip()
    cover_url = str(raw.get("album_image") or raw.get("image") or "")
    album_id = str(raw.get("album_id") or "")
    try:
        track_id = int(raw["id"])
        duration = int(raw.get("duration") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    tags = ((raw.get("musicinfo") or {}).get("tags") or {}).get("genres") or []
    genres = [genre.lower()] + [g.strip().lower() for g in tags if g and g.strip().lower() != genre]
    if not (title and artist and cover_url and album_id.isdigit() and duration >= CLIP_S):
        return None
    if len(title) >= MAX_TITLE_LEN:
        return None
    released = str(raw.get("releasedate") or "")[:4]
    return {
        "id": track_id,
        "title": title,
        "artist": artist,
        "album": str(raw.get("album_name") or "").strip(),
        "album_id": int(album_id),
        "year": int(released) if released.isdigit() and 1900 < int(released) < 2100 else None,
        "genres": genres[:3],
        "duration": duration,
        "license": str(raw.get("license_ccurl") or ""),
        "source_url": str(raw.get("shareurl") or f"https://www.jamendo.com/track/{track_id}"),
        "download_url": str(raw["audiodownload"]),
        "cover_url": cover_url,
    }


def catalog_row(track: dict[str, Any]) -> dict[str, Any]:
    """Строка `catalog-tracks.jsonl`: те же поля, что у FMA, плюс происхождение и обложка."""
    return {
        "key": f"track:jamendo-{track['id']}",
        "kind": "track",
        "title": track["title"],
        "year": track["year"],
        "persons": [{"name": track["artist"], "role": "artist"}],
        "genres": track["genres"],
        "metrics": {"duration_ms": CLIP_S * 1000},
        "album": track["album"],
        "license": track["license"],
        "source_url": track["source_url"],
        "origin": "jamendo",
        "audio": f"{track['id']}.mp3",
        "cover": f"{track['album_id']}.jpg",
    }


def clip_command(ffmpeg: str, src: Path, dst: Path, start_s: int) -> list[str]:
    """Команда ffmpeg: вырезать 30 секунд с `start_s` и перекодировать в mp3 без видео."""
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", str(start_s), "-t", str(CLIP_S), "-i", str(src),
        "-vn", "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", "128k", str(dst),
    ]  # fmt: skip


def is_jpeg(data: bytes) -> bool:
    """Проверка по сигнатуре: обложка должна быть настоящим JPEG."""
    return data[:3] == b"\xff\xd8\xff"


def _get(url: str, *, retries: int = 5, pause: float = 1.0) -> bytes:
    """GET с повтором и нарастающей паузой (429/5xx/сетевые сбои)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 401, 403, 404):
                raise
            time.sleep(pause * (attempt + 1) * 2)
    raise RuntimeError("не удалось получить ответ после повторов")


def fetch_page(client_id: str, genre: str, offset: int, *, pause: float) -> list[dict[str, Any]]:
    """Страница выдачи по жанру. Пустой ответ на живой запрос иногда случается — повторяем."""
    # urlencode кодирует «+» в include как %2B; с «+» как пробелом API отдаёт пустой список.
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "format": "json",
            "limit": PAGE_SIZE,
            "offset": offset,
            "include": "musicinfo+licenses",
            "imagesize": 600,
            "audioformat": "mp32",
            "audiodlformat": "mp32",
            "order": "popularity_total",
            "tags": genre,
        }
    )
    for attempt in range(4):
        time.sleep(pause)
        body = json.loads(_get(f"{API}?{query}"))
        headers = body.get("headers", {})
        if headers.get("status") != "success":
            raise RuntimeError(f"Jamendo: {headers.get('error_message') or headers.get('code')}")
        if body.get("results"):
            return body["results"]
        time.sleep(2 * (attempt + 1))
    return []


def _load_client_id() -> str:
    value = os.environ.get("JAMENDO_CLIENT_ID")
    keys = ROOT / ".keys.env"
    if not value and keys.exists():
        for line in keys.read_text(encoding="utf-8").splitlines():
            name, _, val = line.partition("=")
            if name.strip() == "JAMENDO_CLIENT_ID":
                value = val.strip().strip("\"'")
    if not value:
        sys.exit("нет JAMENDO_CLIENT_ID (окружение или <root>/.keys.env)")
    return value


def download_clip(track: dict[str, Any], dest: Path, ffmpeg: str, tmp_dir: Path) -> bool:
    """Скачивает трек, режет клип и удаляет исходник; `False` — трек пропущен."""
    src = tmp_dir / f"{track['id']}.src.mp3"
    try:
        src.write_bytes(_get(track["download_url"]))
        cmd = clip_command(ffmpeg, src, dest, clip_start(track["duration"]))
        done = subprocess.run(cmd, capture_output=True, check=False)  # noqa: S603
        if done.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            return False
        return True
    finally:
        src.unlink(missing_ok=True)


def download_cover(track: dict[str, Any], dest: Path) -> bool:
    """Скачивает обложку альбома (один файл на альбом); не JPEG — пропуск."""
    if dest.exists() and dest.stat().st_size > 0:
        return True
    data = _get(track["cover_url"])
    if not is_jpeg(data):
        return False
    dest.write_bytes(data)
    return True


def collect(
    client_id: str,
    genres: tuple[str, ...],
    count: int,
    out: Path,
    *,
    ffmpeg: str,
    pause: float,
) -> list[dict[str, Any]]:
    """Набирает `count` треков с равной квотой на жанр; готовое на диске не качает."""
    quota = -(-count // len(genres))
    clips, covers, tmp = out / "clips", out / "covers", out / "tmp"
    for d in (clips, covers, tmp):
        d.mkdir(parents=True, exist_ok=True)
    chosen: dict[int, dict[str, Any]] = {}
    for genre in genres:
        taken, offset = 0, 0
        while taken < quota and len(chosen) < count:
            page = fetch_page(client_id, genre, offset, pause=pause)
            if not page:
                break
            offset += PAGE_SIZE
            for raw in page:
                track = parse_track(raw, genre)
                if track is None or track["id"] in chosen:
                    continue
                dest = clips / f"{track['id']}.mp3"
                try:
                    if not (dest.exists() and dest.stat().st_size > 0):
                        if not download_clip(track, dest, ffmpeg, tmp):
                            continue
                        time.sleep(pause)
                    if not download_cover(track, covers / f"{track['album_id']}.jpg"):
                        continue
                except (RuntimeError, urllib.error.URLError, OSError) as exc:
                    print(f"пропуск {track['id']}: {exc}", flush=True)
                    continue
                chosen[track["id"]] = track
                taken += 1
                if len(chosen) % 25 == 0:
                    print(f"{len(chosen)}/{count} ({genre})", flush=True)
                if taken >= quota or len(chosen) >= count:
                    break
        print(f"жанр {genre}: {taken}", flush=True)
    return list(chosen.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=400)
    ap.add_argument("--genres", default=",".join(GENRES), help="через запятую")
    ap.add_argument("--ffmpeg", default=DEFAULT_FFMPEG)
    ap.add_argument("--pause", type=float, default=0.5, help="пауза между запросами, с")
    args = ap.parse_args()

    genres = tuple(g.strip() for g in args.genres.split(",") if g.strip())
    out = ROOT / "jamendo"
    tracks = collect(
        _load_client_id(), genres, args.count, out, ffmpeg=args.ffmpeg, pause=args.pause
    )
    rows = [catalog_row(t) for t in tracks]
    (out / "catalog-tracks.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    print(f"готово: {len(rows)} треков в {out}")


if __name__ == "__main__":
    main()
