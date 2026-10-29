"""Выгрузка фильмов из TMDB для локального стенда (афиши, кадры, описания, режиссёры).

Только для local/ci: файлы лежат вне репозитория (по умолчанию `C:/gv-media/tmdb`, каталог
задаётся `GV_REAL_MEDIA_DIR`), в git не попадают (ADR 0014). Ключ читается из
`<GV_REAL_MEDIA_DIR>/.keys.env` (строка `TMDB_KEY=...`) и нигде не печатается. Трейлеры не
скачиваются: TMDB отдаёт на них только ссылки YouTube.

    uv run python seeds/media/fetch_tmdb.py --count 150
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"
ROOT = Path(os.environ.get("GV_REAL_MEDIA_DIR", "C:/gv-media"))


def _key() -> str:
    for line in (ROOT / ".keys.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TMDB_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("нет TMDB_KEY в .keys.env")


KEY = _key()


def get(path: str, **params: str) -> dict:
    url = f"{API}{path}?{urllib.parse.urlencode({'api_key': KEY, **params})}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(2 + attempt * 2)
                continue
            if exc.code == 404:
                return {}
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1 + attempt)
    return {}


def download(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                dest.write_bytes(r.read())
            return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1 + attempt)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=150)
    ap.add_argument("--frames", type=int, default=6)
    args = ap.parse_args()

    out = ROOT / "tmdb"
    ids: list[int] = []
    page = 1
    while len(ids) < args.count and page <= 30:
        data = get("/movie/top_rated", language="ru-RU", page=str(page))
        for m in data.get("results", []):
            if m["id"] not in ids and m.get("poster_path") and m.get("release_date"):
                ids.append(m["id"])
        page += 1
        time.sleep(0.05)
    ids = ids[: args.count]

    catalog: list[dict] = []
    for n, mid in enumerate(ids, 1):
        d = get(f"/movie/{mid}", language="ru-RU", append_to_response="credits")
        en = get(f"/movie/{mid}", language="en-US")
        imgs = get(f"/movie/{mid}/images", include_image_language="null,ru,en")
        if not d or not d.get("title"):
            continue
        folder = out / str(mid)
        poster = d.get("poster_path") or ""
        got_poster = bool(poster) and download(f"{IMG}/w500{poster}", folder / "poster.jpg")
        frames: list[str] = []
        backs = sorted(
            imgs.get("backdrops", []),
            key=lambda b: (b.get("iso_639_1") is not None, -float(b.get("vote_average", 0))),
        )
        for i, b in enumerate(backs[: args.frames], 1):
            if download(f"{IMG}/w1280{b['file_path']}", folder / f"frame-{i}.jpg"):
                frames.append(f"frame-{i}.jpg")
        directors = [
            c["name"] for c in d.get("credits", {}).get("crew", []) if c.get("job") == "Director"
        ][:2]
        year = int(d["release_date"][:4]) if d.get("release_date") else None
        title = d["title"]
        original = en.get("original_title") or d.get("original_title")
        catalog.append(
            {
                "key": f"film:tmdb-{mid}",
                "kind": "film",
                "title": title,
                "original_title": original if original != title else None,
                "year": year,
                "aliases": [],
                "persons": [{"name": x, "role": "director"} for x in directors],
                "genres": [g["name"].lower() for g in d.get("genres", [])],
                "metrics": {"vote_average": d.get("vote_average"), "runtime_min": d.get("runtime")},
                "external_refs": {"tmdb": str(mid)},
                "overview": d.get("overview") or en.get("overview") or "",
                "poster": "poster.jpg" if got_poster else None,
                "frames": frames,
            }
        )
        print(f"{n}/{len(ids)} {title} ({year}): кадров {len(frames)}", flush=True)
        time.sleep(0.05)

    (out / "catalog-films.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in catalog) + "\n", encoding="utf-8"
    )
    print(f"готово: {len(catalog)} фильмов в {out}")


if __name__ == "__main__":
    main()
