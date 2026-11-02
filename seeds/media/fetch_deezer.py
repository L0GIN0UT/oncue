"""Выгрузка популярных треков Deezer для ЛОКАЛЬНОГО стенда: превью 30 с, обложки, метаданные.

Только для личной разработки без монетизации: условия Deezer API разрешают исключительно
некоммерческое частное использование. Файлы лежат вне репозитория (по умолчанию
`C:/gv-media/deezer`, каталог задаётся `GV_REAL_MEDIA_DIR`), в git не попадают (ADR 0014).
Ключ не нужен: используется публичное API (лимит около 50 запросов за 5 секунд, скрипт держит
~8 запросов в секунду).

Как работает:
1. Набирает пул артистов: жанры, чарты, имена из `seed-artists.txt`, дальше расширяет по
   «похожим артистам» (приоритет по числу фанатов), пока не наберётся `--artists`.
2. Берёт топ-треки каждого артиста (до 99 штук, поле `rank` = популярность).
3. Убирает дубли одной песни (альбомные и сборные версии, ремастеры, «feat.»), а ремиксы,
   мэшапы, живые и акустические версии оставляет как отдельные треки.
4. Год песни - самый ранний среди альбомов всех её версий. Берёт `--count` самых популярных,
   но сначала добирает квоты по десятилетиям (`--decade-min`, по умолчанию не меньше 600 треков
   на 80-е и 90-е). Если квота не набрана, обход артистов расширяется до `--max-artists`,
   а потом берётся сколько есть: в конце печатается таблица «десятилетие: получено / квота».
5. Скачивает превью (свежая ссылка перед каждым скачиванием), обложки и фото артистов
   (`picture_big` в `deezer/artists/<id>.jpg`; ссылка в строке каталога - `artist_photo`).
Скрипт возобновляемый: кэш в `deezer/cache/`, уже скачанные файлы не качаются заново.

    uv run python fetch_deezer.py --count 10000
"""

from __future__ import annotations

import argparse
import heapq
import http.client
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

API = "https://api.deezer.com"
ROOT = Path(os.environ.get("GV_REAL_MEDIA_DIR", "C:/gv-media"))
OUT = ROOT / "deezer"
CACHE = OUT / "cache"
HERE = Path(__file__).resolve().parent
EXCLUDED_GENRES = {0, 95}  # «All» и «Kids»
GENRE_NAMES = {
    "rap/hip hop": "hip-hop",
    "r&b": "rnb",
    "electro": "electronic",
    "films/games": "soundtrack",
    "soul & funk": "soul",
    "african music": "world",
    "asian music": "world",
    "brazilian music": "world",
    "indian music": "world",
    "latin music": "latin",
    "dance": "dance",
}


class Limiter:
    """Общий для потоков ограничитель: не чаще `rate` запросов в секунду."""

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = self.next_at - now
            self.next_at = max(now, self.next_at) + self.interval
        if delay > 0:
            time.sleep(delay)


LIMITER = Limiter(8.0)


def api_get(path: str, **params: Any) -> dict[str, Any]:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    for attempt in range(6):
        LIMITER.wait()
        req = urllib.request.Request(url, headers={"Accept-Language": "en"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.load(resp)
        except (OSError, http.client.HTTPException, json.JSONDecodeError):
            time.sleep(1 + attempt * 2)
            continue
        error = data.get("error") if isinstance(data, dict) else None
        if error:
            if error.get("code") == 4:  # квота: ждём и повторяем
                time.sleep(5 + attempt * 5)
                continue
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def load_json(name: str, default: Any) -> Any:
    path = CACHE / name
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(name: str, value: Any) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / (name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE / name)


# --- дубли ---------------------------------------------------------------------------------

_SEGMENTS = re.compile(r"\(([^)]*)\)|\[([^\]]*)\]|\s-\s(.+)$")
_DROP = re.compile(
    r"remaster|re-master|radio edit|single version|album version|original version|"
    r"explicit|clean|from\s|soundtrack|feat|ft\.|with\s|mono|stereo|deluxe|bonus|\bversion\b"
)
_KEEP = re.compile(
    r"remix|\bmix\b|mashup|bootleg|live|acoustic|instrumental|unplugged|demo|session|"
    r"cover|rework|flip|\bedit\b|\bvip\b|sped|slowed|extended|club|dub"
)
_BAD_TITLE = re.compile(r"karaoke|tribute|made famous|originally performed|backing track", re.I)


def normalized_title(title: str, version: str) -> str:
    """Ключ песни: название без «шума» версий, но с пометками ремикса, лайва и т. п."""
    text = (
        f"{title} {version}".lower()
        if version and version.lower() not in title.lower()
        else title.lower()
    )
    kept: list[str] = []
    for match in _SEGMENTS.finditer(text):
        segment = next(g for g in match.groups() if g is not None).strip()
        if _DROP.search(segment):
            continue
        if _KEEP.search(segment):
            kept.append(re.sub(r"\W+", " ", segment).strip())
    base = _SEGMENTS.sub(" ", text)
    base = re.sub(r"\W+", " ", base).strip()
    return base + "|" + "|".join(sorted(kept))


# --- пул артистов ---------------------------------------------------------------------------


def seed_artists() -> list[tuple[int, int]]:
    """Стартовые артисты: (id, число фанатов; 0 — неизвестно)."""
    found: dict[int, int] = {}
    genres = [g["id"] for g in api_get("/genre").get("data", []) if g["id"] not in EXCLUDED_GENRES]
    for gid in [0, *genres]:
        for item in api_get(f"/chart/{gid}/artists", limit=100).get("data", []):
            found.setdefault(item["id"], 0)
        if gid:
            for item in api_get(f"/genre/{gid}/artists").get("data", []):
                found.setdefault(item["id"], 0)
    names_file = HERE / "seed-artists.txt"
    if names_file.is_file():
        for line in names_file.read_text(encoding="utf-8").splitlines():
            name = line.strip()
            if not name or name.startswith("#"):
                continue
            hit = (api_get("/search/artist", q=name, limit=1).get("data") or [None])[0]
            if hit:
                found[hit["id"]] = max(found.get(hit["id"], 0), int(hit.get("nb_fan") or 0))
    return list(found.items())


def crawl_artists(target: int) -> dict[str, Any]:
    """Обход по «похожим артистам» с приоритетом по числу фанатов; состояние в кэше."""
    state = load_json("crawl.json", {"visited": {}, "frontier": []})
    visited: dict[str, Any] = state["visited"]
    heap: list[tuple[int, int]] = [(-fans, aid) for fans, aid in state["frontier"]]
    if not visited and not heap:
        for aid, fans in seed_artists():
            heap.append((-fans, aid))
        print(f"стартовых артистов: {len(heap)}", flush=True)
    heapq.heapify(heap)
    queued = {aid for _, aid in heap} | {int(k) for k in visited}

    def fetch(aid: int) -> tuple[int, list[dict[str, Any]], list[dict[str, Any]]]:
        top = api_get(f"/artist/{aid}/top", limit=100).get("data", [])
        related = api_get(f"/artist/{aid}/related").get("data", [])
        return aid, top, related

    while len(visited) < target and heap:
        batch: list[int] = []
        while heap and len(batch) < 24 and len(visited) + len(batch) < target:
            _, aid = heapq.heappop(heap)
            if str(aid) not in visited:
                batch.append(aid)
        if not batch:
            break
        with ThreadPoolExecutor(max_workers=6) as pool:
            for aid, top, related in pool.map(fetch, batch):
                tracks = []
                for t in top:
                    if int((t.get("artist") or {}).get("id") or 0) != aid or not t.get("preview"):
                        continue
                    album = t.get("album") or {}
                    tracks.append(
                        {
                            "id": t["id"],
                            "title": t.get("title") or "",
                            "version": t.get("title_version") or "",
                            "duration": t.get("duration") or 0,
                            "rank": t.get("rank") or 0,
                            "link": t.get("link") or "",
                            "artist_id": aid,
                            "artist": (t.get("artist") or {}).get("name") or "",
                            "artist_picture": (t.get("artist") or {}).get("picture_big") or "",
                            "album_id": album.get("id"),
                            "album": album.get("title") or "",
                            "cover": album.get("cover_big") or "",
                        }
                    )
                visited[str(aid)] = tracks
                for rel in related:
                    rid = rel["id"]
                    if rid not in queued:
                        queued.add(rid)
                        heapq.heappush(heap, (-int(rel.get("nb_fan") or 0), rid))
        save_json("crawl.json", {"visited": visited, "frontier": [(-n, a) for n, a in heap]})
        print(f"артистов обработано: {len(visited)}/{target}, в очереди {len(heap)}", flush=True)
    return visited


# --- отбор ----------------------------------------------------------------------------------


def dedupe_tracks(visited: dict[str, Any]) -> list[dict[str, Any]]:
    """Песни без дублей по убыванию популярности.

    Из версий одной песни остаётся самая популярная, а альбомы всех версий собираются в
    ``album_ids``: год песни берётся самый ранний (хит часто лежит и на сборнике более
    позднего года).
    """
    best: dict[tuple[int, str], dict[str, Any]] = {}
    albums: dict[tuple[int, str], set[int]] = {}
    for tracks in visited.values():
        for t in tracks:
            if _BAD_TITLE.search(t["title"]) or not t["title"] or len(t["title"]) > 80:
                continue
            key = (t["artist_id"], normalized_title(t["title"], t["version"]))
            if t.get("album_id"):
                albums.setdefault(key, set()).add(int(t["album_id"]))
            if key not in best or t["rank"] > best[key]["rank"]:
                best[key] = t
    result = [{**t, "album_ids": sorted(albums.get(k, ()))} for k, t in best.items()]
    return sorted(result, key=lambda x: -x["rank"])


def select_tracks(visited: dict[str, Any], count: int, per_artist: int) -> list[dict[str, Any]]:
    return limit_per_artist(dedupe_tracks(visited), count, per_artist)


def limit_per_artist(
    candidates: Iterable[dict[str, Any]], count: int, per_artist: int
) -> list[dict[str, Any]]:
    """Первые ``count`` кандидатов по порядку, не больше ``per_artist`` на артиста."""
    by_artist: dict[int, int] = {}
    chosen: list[dict[str, Any]] = []
    for t in candidates:
        if by_artist.get(t["artist_id"], 0) >= per_artist:
            continue
        by_artist[t["artist_id"]] = by_artist.get(t["artist_id"], 0) + 1
        chosen.append(t)
        if len(chosen) >= count:
            break
    return chosen


# --- год и квоты по десятилетиям -------------------------------------------------------------


def album_year(info: dict[str, Any] | None) -> int | None:
    """Год выпуска альбома из ``release_date`` (``0000-00-00`` и мусор - нет года)."""
    year = str((info or {}).get("date") or "")[:4]
    return int(year) if year.isdigit() and 1900 < int(year) < 2100 else None


def earliest_year(album_ids: Iterable[int], albums: dict[str, Any]) -> int | None:
    """Самый ранний год среди альбомов всех версий песни."""
    years = [y for a in album_ids if (y := album_year(albums.get(str(a)))) is not None]
    return min(years) if years else None


def parse_quotas(spec: str) -> dict[int, int]:
    """``1980=600,1990=600`` → ``{1980: 600, 1990: 600}`` (начало десятилетия → минимум)."""
    quotas: dict[int, int] = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        decade, _, minimum = part.partition("=")
        if not (decade.strip().isdigit() and minimum.strip().isdigit()):
            raise ValueError(f"квота «{part}»: ожидается ГГГГ=число, например 1980=600")
        quotas[int(decade) // 10 * 10] = int(minimum)
    return quotas


def pick_with_quotas(
    candidates: Iterable[dict[str, Any]],
    albums: dict[str, Any],
    count: int,
    per_artist: int,
    quotas: dict[int, int],
) -> list[dict[str, Any]]:
    """``count`` треков: сначала самые популярные из каждого десятилетия с квотой, затем остальные.

    ``candidates`` отсортированы по популярности. Квота не набрана - берётся сколько есть.
    Лимит ``per_artist`` действует на весь результат. Порядок результата - по популярности.
    """
    ordered = list(candidates)
    reserved: set[int] = set()
    by_artist: dict[int, int] = {}
    for decade, minimum in sorted(quotas.items()):
        taken = 0
        for i, t in enumerate(ordered):
            if taken >= minimum:
                break
            year = earliest_year(t.get("album_ids") or (), albums)
            if year is None or year // 10 * 10 != decade or i in reserved:
                continue
            if by_artist.get(t["artist_id"], 0) >= per_artist:
                continue
            reserved.add(i)
            by_artist[t["artist_id"]] = by_artist.get(t["artist_id"], 0) + 1
            taken += 1
    for i, t in enumerate(ordered):
        if len(reserved) >= count:
            break
        if i in reserved or by_artist.get(t["artist_id"], 0) >= per_artist:
            continue
        reserved.add(i)
        by_artist[t["artist_id"]] = by_artist.get(t["artist_id"], 0) + 1
    return [ordered[i] for i in sorted(reserved)]


def decade_counts(chosen: Iterable[dict[str, Any]], albums: dict[str, Any]) -> dict[int, int]:
    """Сколько треков в каждом десятилетии (0 - год неизвестен)."""
    got: dict[int, int] = {}
    for t in chosen:
        year = earliest_year(t.get("album_ids") or (), albums)
        key = year // 10 * 10 if year else 0
        got[key] = got.get(key, 0) + 1
    return got


def quota_shortfalls(
    chosen: Iterable[dict[str, Any]], albums: dict[str, Any], quotas: dict[int, int]
) -> dict[int, int]:
    """Десятилетия, где квота не набрана: начало десятилетия → сколько не хватает."""
    got = decade_counts(chosen, albums)
    return {d: q - got.get(d, 0) for d, q in quotas.items() if got.get(d, 0) < q}


def quota_table(
    chosen: Iterable[dict[str, Any]], albums: dict[str, Any], quotas: dict[int, int]
) -> list[str]:
    """Строки «десятилетие: получено / квота» по всем десятилетиям выборки и квот."""
    got = decade_counts(chosen, albums)
    lines = []
    for decade in sorted(set(got) | set(quotas)):
        label = f"{decade}-е" if decade else "год неизвестен"
        quota = f"{quotas[decade]}" if decade in quotas else "-"
        mark = " (МЕНЬШЕ КВОТЫ)" if got.get(decade, 0) < quotas.get(decade, 0) else ""
        lines.append(f"  {label}: {got.get(decade, 0)} / {quota}{mark}")
    return lines


def has_artist_photo(url: str) -> bool:
    """У артиста без фото Deezer отдаёт заглушку с пустым хэшем (``/images/artist//``)."""
    return bool(url) and "/artist//" not in url


def display_title(title: str, version: str) -> str:
    """Название трека: версию («feat.», «Remastered») добавляем, только если её ещё нет в названии."""
    version = (version or "").strip()
    if version and version.lower() not in title.lower():
        return f"{title} {version}".strip()
    return title


def enrich_albums(tracks: Iterable[dict[str, Any]], *, limit: int | None = None) -> dict[str, Any]:
    """Даты и жанры альбомов всех версий треков; новых запросов не больше ``limit``."""
    albums: dict[str, Any] = load_json("albums.json", {})
    wanted: set[int] = set()
    for t in tracks:
        wanted.update(t.get("album_ids") or ([t["album_id"]] if t.get("album_id") else []))
    todo = sorted(a for a in wanted if str(a) not in albums)
    if limit is not None:
        todo = todo[:limit]

    def fetch(album_id: int) -> tuple[int, dict[str, Any]]:
        data = api_get(f"/album/{album_id}")
        names = [g["name"] for g in (data.get("genres") or {}).get("data", [])]
        return album_id, {"date": data.get("release_date") or "", "genres": names}

    for i in range(0, len(todo), 240):
        with ThreadPoolExecutor(max_workers=6) as pool:
            for album_id, info in pool.map(fetch, todo[i : i + 240]):
                albums[str(album_id)] = info
        save_json("albums.json", albums)
        print(f"альбомов: {min(i + 240, len(todo))}/{len(todo)}", flush=True)
    return albums


def genre_key(names: list[str]) -> str:
    if not names:
        return "pop"
    raw = names[0].lower()
    return GENRE_NAMES.get(raw, re.sub(r"[^a-z0-9]+", "-", raw).strip("-") or "pop")


# --- скачивание -----------------------------------------------------------------------------


def _curl(url: str, dest: Path) -> bool:
    """Запасной путь: у части CDN Deezer цепочка сертификатов не знакома Python на Windows."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    result = subprocess.run(
        ["curl", "-sfL", "--max-time", "60", "-o", str(tmp), url], check=False, capture_output=True
    )
    if result.returncode == 0 and tmp.is_file():
        tmp.replace(dest)
        return True
    tmp.unlink(missing_ok=True)
    return False


def download(url: str, dest: Path, min_bytes: int) -> bool:
    if dest.is_file() and dest.stat().st_size >= min_bytes:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                body = resp.read()
            if len(body) >= min_bytes:
                dest.write_bytes(body)
                return True
        except urllib.error.URLError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                if _curl(url, dest) and dest.stat().st_size >= min_bytes:
                    return True
                dest.unlink(missing_ok=True)
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(1 + attempt * 2)
    return False


def fetch_files(tracks: list[dict[str, Any]]) -> set[int]:
    """Превью (свежая ссылка перед скачиванием) и обложки; вернуть id скачанных треков."""
    done: set[int] = set()
    lock = threading.Lock()
    covers_done: set[str] = set()

    def work(t: dict[str, Any]) -> None:
        clip = OUT / "clips" / f"{t['id']}.mp3"
        if not (clip.is_file() and clip.stat().st_size > 20_000):
            fresh = api_get(f"/track/{t['id']}").get("preview") or ""
            if not fresh or not download(fresh, clip, 20_000):
                return
        cover = OUT / "covers" / f"{t['album_id']}.jpg"
        key = str(t["album_id"])
        with lock:
            need_cover = key not in covers_done
            covers_done.add(key)
        if need_cover and t["cover"] and not download(t["cover"], cover, 2_000):
            return
        with lock:
            done.add(t["id"])
            if len(done) % 250 == 0:
                print(f"скачано треков: {len(done)}/{len(tracks)}", flush=True)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, tracks))
    return done


def fetch_artist_photos(tracks: Iterable[dict[str, Any]]) -> set[int]:
    """Фото артистов (``picture_big``) в ``deezer/artists/<id>.jpg``; вернуть id скачанных.

    Ссылка берётся из обхода (``artist_picture``), а если кэш обхода старый и поля нет,
    запрашивается ``/artist/<id>``. Заглушка «нет фото» не скачивается.
    """
    urls: dict[int, str] = {}
    for t in tracks:
        urls.setdefault(t["artist_id"], t.get("artist_picture") or "")
    done: set[int] = set()
    lock = threading.Lock()

    def work(item: tuple[int, str]) -> None:
        artist_id, url = item
        dest = OUT / "artists" / f"{artist_id}.jpg"
        if not (dest.is_file() and dest.stat().st_size >= 2_000):
            url = url or api_get(f"/artist/{artist_id}").get("picture_big") or ""
            if not has_artist_photo(url) or not download(url, dest, 2_000):
                return
        with lock:
            done.add(artist_id)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, urls.items()))
    print(f"фото артистов: {len(done)}/{len(urls)}", flush=True)
    return done


def choose_tracks(args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Обход, дедупликация, альбомы и выбор треков с квотами по десятилетиям.

    Альбомы подгружаются порциями по убыванию популярности и только пока квоты или общее число
    не набраны. Квоты не набраны на всём пуле - обход расширяется (до ``--max-artists``).
    """
    quotas = parse_quotas(args.decade_min)
    target = args.artists
    while True:
        visited = crawl_artists(target)
        candidates = dedupe_tracks(visited)
        albums: dict[str, Any] = load_json("albums.json", {})
        spent, seen = 0, 0
        chosen: list[dict[str, Any]] = []
        while seen < len(candidates):
            chunk = candidates[seen : seen + 600]
            seen += len(chunk)
            before = len(albums)
            albums = enrich_albums(chunk, limit=max(args.album_budget - spent, 0))
            spent += len(albums) - before
            chosen = pick_with_quotas(
                candidates[:seen], albums, args.count, args.per_artist, quotas
            )
            if len(chosen) >= args.count and not quota_shortfalls(chosen, albums, quotas):
                break
            if spent >= args.album_budget:
                break
        short = quota_shortfalls(chosen, albums, quotas)
        grown = target < args.max_artists and len(visited) >= target
        if not short or not grown:
            break
        print(f"квоты не набраны {short}: расширяю обход артистов", flush=True)
        target = min(target + 1000, args.max_artists)
    print("десятилетия: получено / квота", flush=True)
    for line in quota_table(chosen, albums, quotas):
        print(line, flush=True)
    if short:
        print(f"ВНИМАНИЕ: квоты не набраны полностью (не хватает {short}), берём сколько есть")
    return chosen, albums


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10000, help="сколько треков оставить")
    ap.add_argument("--artists", type=int, default=3000, help="сколько артистов обойти")
    ap.add_argument(
        "--max-artists", type=int, default=6000, help="до скольких расширять обход ради квот"
    )
    ap.add_argument("--per-artist", type=int, default=40, help="максимум треков одного артиста")
    ap.add_argument(
        "--decade-min",
        default="1980=600,1990=600",
        help="минимум треков по десятилетиям, ГГГГ=число через запятую (пусто - без квот)",
    )
    ap.add_argument(
        "--album-budget", type=int, default=30000, help="максимум новых запросов /album за запуск"
    )
    args = ap.parse_args()

    chosen, albums = choose_tracks(args)
    print(f"после удаления дублей выбрано треков: {len(chosen)}", flush=True)
    done = fetch_files(chosen)
    photos = fetch_artist_photos(t for t in chosen if t["id"] in done)

    rows = []
    for t in chosen:
        if t["id"] not in done:
            continue
        info = albums.get(str(t["album_id"])) or {}
        year = earliest_year(t.get("album_ids") or [t["album_id"]], albums)
        rows.append(
            {
                "key": f"track:deezer-{t['id']}",
                "kind": "track",
                "title": display_title(t["title"], t["version"]),
                "year": year,
                "persons": [{"name": t["artist"], "role": "artist"}],
                "genres": [genre_key(info.get("genres") or [])],
                "metrics": {"duration_ms": 30000, "listens": t["rank"]},
                "album": t["album"],
                "license": "",
                "source_url": t["link"],
                "origin": "deezer",
                "audio": f"{t['id']}.mp3",
                "cover": f"{t['album_id']}.jpg",
                **(
                    {"artist_photo": f"artists/{t['artist_id']}.jpg"}
                    if t["artist_id"] in photos
                    else {}
                ),
            }
        )
    (OUT / "catalog-tracks.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    print(f"готово: {len(rows)} треков в {OUT}")


if __name__ == "__main__":
    main()
