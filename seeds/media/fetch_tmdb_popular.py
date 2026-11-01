"""Выгрузка популярного кино TMDB для локального стенда: фильмы, мультфильмы, сериалы, аниме.

Файлы лежат вне репозитория (по умолчанию `C:/gv-media/tmdb`, каталог задаётся
`GV_REAL_MEDIA_DIR`), в git не попадают (ADR 0014). Ключ читается из
`<GV_REAL_MEDIA_DIR>/.keys.env` (строка `TMDB_KEY=...`) и нигде не печатается.

Раскладка:

    tmdb/Аниме/<movie|tv>-<id>/poster.jpg, frame-1..6.jpg   + tmdb/Аниме/catalog.jsonl
    tmdb/Мультфильмы/...     tmdb/Сериалы/...     tmdb/Фильмы/...
    tmdb/catalog-films.jsonl   (все категории вместе)

Категории (порядок важен: тайтл попадает в первую подходящую):
  Аниме        — анимация (жанр 16) на японском языке (фильмы и сериалы);
  Мультфильмы  — остальная анимация (фильмы и сериалы);
  Сериалы      — сериалы без анимации;
  Фильмы       — фильмы без анимации.

В каждой категории не меньше `--min-per-category` (150) тайтлов: если по порогу голосов не
набралось, скрипт добирает по жанрам. Общее число не меньше `--total` (10000): недостающее
добирается в «Фильмах» и «Сериалах». Оставляются только тайтлы с афишей и полным набором кадров.
Скрипт возобновляемый: уже скачанные файлы не качаются заново.

    uv run python fetch_tmdb_popular.py --total 10000
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
ROOT = Path(os.environ.get("GV_REAL_MEDIA_DIR", "C:/gv-media"))
FRAMES = 6
ANIMATION = "16"
VOTE_STEPS = (300, 100, 30, 10, 3)
ORDER = ("Аниме", "Мультфильмы", "Сериалы", "Фильмы")

_lock = threading.Lock()
_next = [0.0]
_key_cache: list[str] = []


def key() -> str:
    if not _key_cache:
        for line in (ROOT / ".keys.env").read_text(encoding="utf-8").splitlines():
            if line.startswith("TMDB_KEY="):
                _key_cache.append(line.split("=", 1)[1].strip())
                break
        else:
            raise SystemExit("нет TMDB_KEY в .keys.env")
    return _key_cache[0]


def _pace(rate: float = 20.0) -> None:
    with _lock:
        now = time.monotonic()
        delay = _next[0] - now
        _next[0] = max(now, _next[0]) + 1.0 / rate
    if delay > 0:
        time.sleep(delay)


def get(path: str, **params: str) -> dict[str, Any]:
    url = f"{API}{path}?{urllib.parse.urlencode({'api_key': key(), **params})}"
    for attempt in range(5):
        _pace()
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.load(resp)  # type: ignore[no-any-return]
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(2 + attempt * 2)
                continue
            if exc.code == 404:
                return {}
            time.sleep(1 + attempt)
        except (OSError, http.client.HTTPException, json.JSONDecodeError):
            time.sleep(1 + attempt)
    return {}


def _curl(url: str, dest: Path) -> bool:
    """Запасной путь: у части CDN цепочка сертификатов не знакома Python на Windows."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    result = subprocess.run(
        ["curl", "-sfL", "--max-time", "60", "-o", str(tmp), url], check=False, capture_output=True
    )
    if result.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
        tmp.replace(dest)
        return True
    tmp.unlink(missing_ok=True)
    return False


def download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                dest.write_bytes(resp.read())
            return True
        except urllib.error.URLError as exc:
            if "CERTIFICATE_VERIFY_FAILED" in str(exc) and _curl(url, dest):
                return True
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(1 + attempt)
    return False


# --- один тайтл -----------------------------------------------------------------------------


def build_row(media: str, mid: int, category: str, out: Path) -> dict[str, Any] | None:
    """Метаданные и файлы одного тайтла; None — если афиши или полного набора кадров нет."""
    extra = {"append_to_response": "credits"} if media == "movie" else {}
    details = get(f"/{media}/{mid}", language="ru-RU", **extra)
    title = details.get("title") or details.get("name")
    date = details.get("release_date") or details.get("first_air_date")
    if not details or not title or not date:
        return None
    english = get(f"/{media}/{mid}", language="en-US")
    images = get(f"/{media}/{mid}/images", include_image_language="null,ru,en")
    folder = out / category / f"{media}-{mid}"
    poster = details.get("poster_path") or ""
    if not poster or not download(f"{IMG}/w500{poster}", folder / "poster.jpg"):
        return None
    backdrops = sorted(
        images.get("backdrops", []),
        key=lambda b: (b.get("iso_639_1") is not None, -float(b.get("vote_average", 0))),
    )
    frames: list[str] = []
    for i, backdrop in enumerate(backdrops[:FRAMES], 1):
        if download(f"{IMG}/w1280{backdrop['file_path']}", folder / f"frame-{i}.jpg"):
            frames.append(f"frame-{i}.jpg")
    if len(frames) < FRAMES:
        return None
    if media == "movie":
        people = [
            c["name"]
            for c in details.get("credits", {}).get("crew", [])
            if c.get("job") == "Director"
        ][:2]
        role = "director"
    else:
        people = [c["name"] for c in details.get("created_by", [])][:2]
        role = "creator"
    original = (
        english.get("original_title")
        or english.get("original_name")
        or details.get("original_title")
        or details.get("original_name")
    )
    runtime = details.get("runtime") or (details.get("episode_run_time") or [None])[0]
    return {
        "key": f"film:tmdb-{mid}" if media == "movie" else f"film:tmdb-tv-{mid}",
        "kind": "film",
        "category": category,
        "media_type": media,
        "title": title,
        "original_title": original if original != title else None,
        "year": int(str(date)[:4]),
        "aliases": [],
        "persons": [{"name": x, "role": role} for x in people],
        "genres": [g["name"].lower() for g in details.get("genres", [])],
        "metrics": {
            "vote_average": details.get("vote_average"),
            "vote_count": details.get("vote_count"),
            "runtime_min": runtime,
            "popularity": details.get("popularity"),
            "seasons": details.get("number_of_seasons"),
        },
        "external_refs": {"tmdb": str(mid), "tmdb_type": media},
        "overview": details.get("overview") or english.get("overview") or "",
        "folder": f"{category}/{media}-{mid}",
        "poster": "poster.jpg",
        "frames": frames,
    }


# --- выбор тайтлов --------------------------------------------------------------------------

# (тип TMDB, постоянные параметры discover, исключать японский язык)
SOURCES: dict[str, list[tuple[str, dict[str, str], bool]]] = {
    "Аниме": [
        ("movie", {"with_genres": ANIMATION, "with_original_language": "ja"}, False),
        ("tv", {"with_genres": ANIMATION, "with_original_language": "ja"}, False),
    ],
    "Мультфильмы": [
        ("movie", {"with_genres": ANIMATION}, True),
        ("tv", {"with_genres": ANIMATION}, True),
    ],
    "Сериалы": [("tv", {"without_genres": ANIMATION}, False)],
    "Фильмы": [("movie", {"without_genres": ANIMATION}, False)],
}


class State:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.cache_path = out / "cache-films.json"
        data = (
            json.loads(self.cache_path.read_text(encoding="utf-8"))
            if self.cache_path.is_file()
            else {}
        )
        self.rows: dict[str, dict[str, Any]] = data.get("rows", {})
        self.skipped: set[str] = set(data.get("skipped", []))

    def seen(self, media: str, mid: int) -> bool:
        k = f"{media}:{mid}"
        return k in self.rows or k in self.skipped

    def count(self, category: str) -> int:
        return sum(1 for r in self.rows.values() if r["category"] == category)

    def total(self) -> int:
        return len(self.rows)

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"rows": self.rows, "skipped": sorted(self.skipped)}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.cache_path)


def genre_ids() -> list[str]:
    found: set[str] = set()
    for media in ("movie", "tv"):
        for g in get(f"/genre/{media}/list", language="ru-RU").get("genres", []):
            found.add(str(g["id"]))
    found.discard(ANIMATION)
    return sorted(found)


def fill(
    state: State, category: str, want: int, votes: int, extra: dict[str, str] | None = None
) -> None:
    """Добрать категорию до `want` тайтлов: страницы discover по убыванию популярности."""
    sources = SOURCES[category]
    exhausted = [False] * len(sources)
    page = 1
    while state.count(category) < want and page <= 500 and not all(exhausted):
        batch: list[tuple[str, int]] = []
        for i, (media, params, no_ja) in enumerate(sources):
            if exhausted[i]:
                continue
            merged = dict(params)
            if extra and "with_genres" in extra:
                base = merged.get("with_genres")
                merged["with_genres"] = (
                    f"{base},{extra['with_genres']}" if base else extra["with_genres"]
                )
            listing = get(
                f"/discover/{media}",
                language="ru-RU",
                sort_by="popularity.desc",
                include_adult="false",
                page=str(page),
                **{"vote_count.gte": str(votes)},
                **merged,
            )
            results = listing.get("results", [])
            if not results or page >= int(listing.get("total_pages", 1)):
                exhausted[i] = True
            for item in results:
                if no_ja and item.get("original_language") == "ja":
                    continue
                if not state.seen(media, item["id"]):
                    batch.append((media, item["id"]))
        need = want - state.count(category)
        batch = batch[: max(need, 0) + 8]
        if batch:
            with ThreadPoolExecutor(max_workers=8) as pool:
                built = list(pool.map(lambda t: build_row(t[0], t[1], category, state.out), batch))
            for (media, mid), row in zip(batch, built, strict=True):
                if row is None:
                    state.skipped.add(f"{media}:{mid}")
                elif state.count(category) < want:
                    state.rows[f"{media}:{mid}"] = row
        page += 1
        if page % 3 == 0:
            state.save()
            print(
                f"{category}: {state.count(category)}/{want} (порог голосов {votes}, страница {page})",
                flush=True,
            )
    state.save()


def reach(state: State, category: str, want: int) -> None:
    for votes in VOTE_STEPS:
        if state.count(category) >= want:
            return
        fill(state, category, want, votes)


def reach_minimum(state: State, category: str, minimum: int) -> None:
    """Добор по жанрам: у категории всё ещё меньше минимума."""
    if state.count(category) >= minimum:
        return
    print(f"{category}: {state.count(category)} < {minimum}, добираю по жанрам", flush=True)
    for gid in genre_ids():
        if state.count(category) >= minimum:
            break
        fill(state, category, minimum, 1, {"with_genres": gid})


def write_catalogs(state: State) -> None:
    out = state.out
    all_rows: list[dict[str, Any]] = []
    for category in ORDER:
        rows = [r for r in state.rows.values() if r["category"] == category]
        rows.sort(key=lambda r: -(r["metrics"].get("popularity") or 0))
        (out / category).mkdir(parents=True, exist_ok=True)
        (out / category / "catalog.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        all_rows.extend(rows)
    (out / "catalog-films.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n", encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--total", type=int, default=10000, help="сколько тайтлов не меньше")
    ap.add_argument("--anime", type=int, default=700)
    ap.add_argument("--cartoons", type=int, default=900, help="мультфильмы")
    ap.add_argument("--series", type=int, default=2400)
    ap.add_argument("--movies", type=int, default=6000)
    ap.add_argument("--min-per-category", type=int, default=150)
    args = ap.parse_args()
    quotas = {
        "Аниме": args.anime,
        "Мультфильмы": args.cartoons,
        "Сериалы": args.series,
        "Фильмы": args.movies,
    }

    state = State(ROOT / "tmdb")
    for category in ORDER:
        reach(state, category, max(quotas[category], args.min_per_category))
        reach_minimum(state, category, args.min_per_category)
        print(f"{category}: итого {state.count(category)}", flush=True)
    # общее число: добор «Фильмов» и «Сериалов» с понижением порога голосов
    for category in ("Фильмы", "Сериалы"):
        deficit = args.total - state.total()
        if deficit <= 0:
            break
        reach(state, category, state.count(category) + deficit)
    write_catalogs(state)
    summary = ", ".join(f"{c} {state.count(c)}" for c in ORDER)
    print(f"готово: {state.total()} тайтлов ({summary}) в {state.out}")


if __name__ == "__main__":
    main()
