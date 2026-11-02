"""Скан уже выгруженного кино TMDB: у каких фильмов среди кадров «по кадру» есть кадры с языком.

Причина (проблема 54): старая выгрузка брала шесть кадров, отсортировав их «чистые первыми», но
при нехватке чистых добирала кадры с `iso_639_1` (афишные композиции, заставки с названием), и в
режиме «по кадру» вместо кадра оказывалась афиша. Скрипт ничего не заменяет на стенде и не пишет
в `tmdb/` и `deezer/`: он только перечисляет затронутые фильмы и готовит чистые кадры.

Для каждой строки `<root>/tmdb/catalog-films.jsonl` скрипт заново запрашивает у TMDB список
картинок и повторяет старый выбор шести кадров. Если среди них есть кадр с языком, фильм
затронут: по новым правилам (`fetch_tmdb_popular.pick_frames`: чистые кадры, добор с языком не
больше двух и только с оценкой не ниже `--frame-min-vote`, повторы афиши отбрасываются) набираются
шесть кадров в `<root>/tmdb-fixed/<folder>/frame-1..6.jpg`. Шесть не набралось - фильм помечается
«нужен ручной выбор или удалить», замена не придумывается.

Выход:
    <root>/tmdb/affected-frames.txt   список затронутых фильмов (табуляция: ключ, папка, статус)
    <root>/tmdb-fixed/                новые кадры + scan-state.json (возобновление)

Набор картинок TMDB мог измениться со времени выгрузки, поэтому «старый выбор» - реконструкция по
текущему ответу API, а не точная копия файлов стенда. Ключ TMDB читается из `<root>/.keys.env`
и нигде не печатается; запросы к API - не чаще 20 в секунду. Скрипт возобновляемый.

    uv run python scan_frame_langs.py [--root C:/gv-media] [--limit 50]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fetch_tmdb_popular as ft  # noqa: E402 - путь добавлен строкой выше

REPORT_NAME = "affected-frames.txt"
STATE_NAME = "scan-state.json"
MANUAL = "нужен ручной выбор или удалить"


def old_selection(backdrops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Шесть кадров, которые брала старая выгрузка (без языка первыми, по оценке)."""
    ordered = sorted(
        backdrops,
        key=lambda b: (b.get("iso_639_1") is not None, -float(b.get("vote_average") or 0)),
    )
    return ordered[: ft.FRAMES]


def language_positions(backdrops: list[dict[str, Any]]) -> list[int]:
    """Номера (с 1) кадров старого выбора, у которых задан язык."""
    return [
        i for i, b in enumerate(old_selection(backdrops), start=1) if b.get("iso_639_1") is not None
    ]


def process(
    row: dict[str, Any],
    *,
    root: Path,
    get_backdrops: Callable[[str, str], list[dict[str, Any]] | None],
    min_vote: float,
) -> dict[str, Any] | None:
    """Проверить один фильм; None - нет данных TMDB (не записывать, повторить в следующий раз)."""
    media = str(row.get("media_type") or "movie")
    mid = str((row.get("external_refs") or {}).get("tmdb") or "")
    folder = str(row["folder"])
    backdrops = get_backdrops(media, mid) if mid else None
    if not backdrops:
        return None
    positions = language_positions(backdrops)
    result: dict[str, Any] = {"key": row["key"], "folder": folder, "positions": positions}
    if not positions:
        return result | {"status": "ok"}
    fixed = root / "tmdb-fixed" / folder
    poster = root / "tmdb" / folder / "poster.jpg"
    base = (root / "tmdb-fixed").resolve()
    if base not in fixed.resolve().parents:  # пустая папка или «..» в каталоге: не выходим из base
        raise ValueError(f"недопустимая папка фильма: {folder!r}")
    # собранное прошлым запуском не считаем: набираем заново в пустой папке
    shutil.rmtree(fixed, ignore_errors=True)
    frames = ft.collect_frames(backdrops, fixed, min_vote=min_vote, poster=poster)
    if frames is None:
        shutil.rmtree(fixed, ignore_errors=True)  # половина комплекта только путает
        return result | {"status": "manual"}
    return result | {"status": "fixed"}


def report_lines(results: dict[str, dict[str, Any]], checked: int) -> list[str]:
    """Содержимое affected-frames.txt: заголовок и строка на каждый затронутый фильм."""
    affected = [r for r in results.values() if r["status"] in ("fixed", "manual")]
    fixed = sum(r["status"] == "fixed" for r in affected)
    lines = [
        f"# проверено фильмов: {checked}, затронуто: {len(affected)} "
        f"(новые кадры готовы: {fixed}, {MANUAL}: {len(affected) - fixed})",
        "# ключ<TAB>папка<TAB>кадры с языком в старом выборе<TAB>статус",
    ]
    for r in sorted(affected, key=lambda x: x["key"]):
        positions = ",".join(map(str, r["positions"]))
        status = f"новые кадры: tmdb-fixed/{r['folder']}" if r["status"] == "fixed" else MANUAL
        lines.append(f"{r['key']}\t{r['folder']}\t{positions}\t{status}")
    return lines


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}  # type: ignore[no-any-return]


def save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def write_report(root: Path, results: dict[str, dict[str, Any]], checked: int) -> Path:
    path = root / "tmdb" / REPORT_NAME
    text = "\n".join(report_lines(results, checked)) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def api_backdrops(media: str, mid: str) -> list[dict[str, Any]] | None:
    """Список backdrops фильма из TMDB (None - ответа нет). Ключ в вывод не попадает."""
    images = ft.get(f"/{media}/{mid}/images", include_image_language="null,ru,en")
    return images.get("backdrops") if images else None


def scan(
    root: Path, *, limit: int | None, workers: int, min_vote: float, fresh: bool = False
) -> None:
    ft.ROOT = root
    catalog = root / "tmdb" / "catalog-films.jsonl"
    rows = [
        json.loads(line) for line in catalog.read_text(encoding="utf-8").split("\n") if line.strip()
    ]
    if limit:
        rows = rows[:limit]
    state_path = root / "tmdb-fixed" / STATE_NAME
    results = {} if fresh else load_state(state_path)
    todo = [r for r in rows if r["key"] not in results]
    print(f"фильмов {len(rows)}, уже проверено {len(rows) - len(todo)}, осталось {len(todo)}")
    lock = threading.Lock()
    counter = [0]

    def work(row: dict[str, Any]) -> None:
        try:
            result = process(row, root=root, get_backdrops=api_backdrops, min_vote=min_vote)
        except (
            OSError,
            ValueError,
        ) as exc:  # диск, сеть или папка вне корня: фильм повторится при следующем запуске
            print(f"{row['key']}: пропущен ({type(exc).__name__})", flush=True)
            return
        with lock:
            if result is not None:
                results[row["key"]] = result
            counter[0] += 1
            if counter[0] % 200 == 0:
                save_state(state_path, results)
                print(f"проверено {counter[0]}/{len(todo)}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, todo))
    save_state(state_path, results)
    in_scope = {r["key"] for r in rows}
    scoped = {k: v for k, v in results.items() if k in in_scope}
    report = write_report(root, scoped, len(scoped))
    affected = sum(v["status"] in ("fixed", "manual") for v in scoped.values())
    print(f"готово: проверено {len(scoped)}, затронуто {affected}; список: {report}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--root", type=Path, default=Path(os.environ.get("GV_REAL_MEDIA_DIR", "C:/gv-media"))
    )
    ap.add_argument("--limit", type=int, default=None, help="проверить только первые N фильмов")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="игнорировать scan-state.json (нужно после смены --frame-min-vote)",
    )
    ap.add_argument("--frame-min-vote", type=float, default=ft.FRAME_MIN_VOTE)
    args = ap.parse_args()
    scan(
        args.root,
        limit=args.limit,
        workers=args.workers,
        min_vote=args.frame_min_vote,
        fresh=args.fresh,
    )


if __name__ == "__main__":
    main()
