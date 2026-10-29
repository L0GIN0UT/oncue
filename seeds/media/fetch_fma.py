"""Выгрузка N треков из открытого датасета Free Music Archive (fma_small) для локального стенда.

Только для local/ci: клипы по 30 секунд под лицензиями Creative Commons лежат вне
репозитория (по умолчанию `C:/gv-media/fma`, каталог задаётся `GV_REAL_MEDIA_DIR`), в git не
попадают (ADR 0014). Архив на 7,7 ГБ целиком не качается: сервер отдаёт диапазоны, поэтому
`zipfile` читает его по HTTP и достаёт только нужные файлы.

    uv run python seeds/media/fetch_fma.py --count 1000

Метаданные (`fma_metadata/tracks.csv`) должны быть распакованы в `<root>/fma/meta`.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import time
import urllib.request
import zipfile
from pathlib import Path

URL = "https://os.unil.cloud.switch.ch/fma/fma_small.zip"
ROOT = Path(os.environ.get("GV_REAL_MEDIA_DIR", "C:/gv-media"))
CHUNK = 4 * 1024 * 1024


class RangeFile(io.RawIOBase):
    """Файлоподобный объект поверх HTTP Range с небольшим кэшем последнего блока."""

    def __init__(self, url: str) -> None:
        self.url = url
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            self.size = int(r.headers["Content-Length"])
        self.pos = 0
        self._cache: tuple[int, bytes] = (-1, b"")

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def _fetch(self, start: int, end: int) -> bytes:
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read()
            except Exception:  # noqa: BLE001 — сетевые сбои повторяем, скрипт разовый
                time.sleep(1 + attempt * 2)
        raise RuntimeError("не удалось прочитать диапазон")

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = self.size - self.pos
        n = min(n, self.size - self.pos)
        if n <= 0:
            return b""
        start, cached = self._cache
        if start <= self.pos and self.pos + n <= start + len(cached):
            data = cached[self.pos - start : self.pos - start + n]
        else:
            fetch_len = max(n, CHUNK)
            end = min(self.pos + fetch_len, self.size) - 1
            block = self._fetch(self.pos, end)
            self._cache = (self.pos, block)
            data = block[:n]
        self.pos += len(data)
        return data


def load_meta() -> dict[int, dict]:
    path = ROOT / "fma" / "meta" / "fma_metadata" / "tracks.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = csv.reader(fh)
        top, sub = next(rows), next(rows)
        next(rows)
        idx = {(t, s): i for i, (t, s) in enumerate(zip(top, sub))}
        out: dict[int, dict] = {}
        for row in rows:
            if not row or not row[0].isdigit():
                continue
            if row[idx[("set", "subset")]] != "small":
                continue
            created = row[idx[("track", "date_created")]]
            released = row[idx[("album", "date_released")]]
            year = (released or created or "")[:4]
            out[int(row[0])] = {
                "title": html.unescape(row[idx[("track", "title")]]).strip(),
                "artist": html.unescape(row[idx[("artist", "name")]]).strip(),
                "album": html.unescape(row[idx[("album", "title")]]).strip(),
                "genre": row[idx[("track", "genre_top")]].strip().lower(),
                "license": row[idx[("track", "license")]].strip(),
                "year": int(year) if year.isdigit() and 1900 < int(year) < 2030 else None,
                "listens": int(row[idx[("track", "listens")]] or 0),
            }
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    meta = load_meta()
    usable = [
        i
        for i, m in meta.items()
        if m["title"] and m["artist"] and m["genre"] and len(m["title"]) < 60
    ]
    out = ROOT / "fma" / "clips"
    out.mkdir(parents=True, exist_ok=True)
    zf = zipfile.ZipFile(RangeFile(URL))
    names = {int(Path(n).stem): n for n in zf.namelist() if n.endswith(".mp3")}
    # Файлы читаем подряд по порядку в архиве: сплошное чтение быстрее случайных диапазонов
    # на порядок (сервер отдаёт ~0,5 МБ/с, а на каждый случайный запрос уходит секунды).
    in_zip = [i for i in usable if i in names]
    in_zip.sort(key=lambda i: zf.getinfo(names[i]).header_offset)
    wanted = in_zip[: args.count]

    catalog = []
    done = 0
    for tid in wanted:
        name = names.get(tid)
        if not name:
            continue
        dest = out / f"{tid:06d}.mp3"
        if not dest.exists() or dest.stat().st_size == 0:
            try:
                dest.write_bytes(zf.read(name))
            except Exception as exc:  # noqa: BLE001
                print(f"пропуск {tid}: {exc}", flush=True)
                continue
        m = meta[tid]
        catalog.append(
            {
                "key": f"track:fma-{tid}",
                "kind": "track",
                "title": m["title"],
                "year": m["year"],
                "persons": [{"name": m["artist"], "role": "artist"}],
                "genres": [m["genre"]],
                "metrics": {"duration_ms": 30000, "listens": m["listens"]},
                "album": m["album"],
                "license": m["license"],
                "audio": dest.name,
            }
        )
        done += 1
        if done % 25 == 0:
            print(f"{done}/{len(wanted)}", flush=True)

    (ROOT / "fma" / "catalog-tracks.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in catalog) + "\n", encoding="utf-8"
    )
    print(f"готово: {len(catalog)} треков в {out}")


if __name__ == "__main__":
    main()
