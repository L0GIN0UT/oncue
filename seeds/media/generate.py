"""Синтетические исходники ассетов для local/e2e: тоны для треков, кадры и звук для фильмов.

Никаких записей и кадров третьих лиц (ADR 0014): каждый трек — синус своей частоты с
огибающей, каждый кадр — градиент с геометрическими фигурами, цвета выводятся из ключа
единицы каталога. Результат детерминирован, существующие файлы не перегенерируются
(повторный засев шлёт те же байты), поэтому бинарные файлы в git не хранятся.

Этап 3a (план §10): на фильм — шесть разных кадров ``film-<slug>-{1..6}.jpg``
(``frame_sequence``, ``position`` = номер), саундтрек ``soundtrack-<slug>.m4a`` и звук фильма
``clip-<slug>.m4a`` (``audio_blackout``) по 50 с; тоны треков — 50 с, чтобы хватило профилю
``audio_reverse/default`` (``start_s`` 30 + 15 с). Читаются все ``seeds/catalog/*.jsonl``.

Запуск::

    uv run python seeds/media/generate.py [--out seeds/media/out] [--catalog seeds/catalog]

Нужны ``ffmpeg`` (как в media-pipeline) и Pillow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
CATALOG = HERE.parent / "catalog"
TONE_SECONDS = 50
# Саундтрек и звук фильма — тоже 50 с: статическим профилям хватит 15 с, но запрос ассетов
# (динамический профиль, ADR 0025) может сдвинуть ``start_s`` — запас как у тонов треков.
FILM_AUDIO_SECONDS = 50
FRAMES_PER_FILM = 6
FRAME_SIZE = (1280, 720)


def _rows(directory: Path = CATALOG) -> list[dict[str, object]]:
    """Строки всех файлов импорта каталога (``*.jsonl``) — сиды этапа 2 и синтетика 3a."""
    rows: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.jsonl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        rows += [json.loads(line) for line in lines if line.strip() and not line.startswith("#")]
    return rows


def _slug(key: str) -> str:
    return key.replace(":", "-")


def _short(key: str) -> str:
    """Ключ без вида: ``film:interstellar`` → ``interstellar``."""
    return key.split(":", 1)[-1].replace(":", "-")


def _seed(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def _ffmpeg_audio(expr: str, seconds: int, path: Path) -> Path:
    if path.exists():
        return path
    # ``-map_metadata -1`` и bitexact: без времени создания и версии кодировщика в теге,
    # иначе повторная генерация даёт другие байты (media-pipeline: 409 key_reused).
    subprocess.run(  # noqa: S603 — аргументы формируются здесь же, без ввода пользователя
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",  # noqa: S607
         "-i", f"aevalsrc={expr}:s=44100:d={seconds}",
         "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
         "-c:a", "aac", "-b:a", "96k", str(path)],
        check=True,
    )  # fmt: skip
    return path


def tone(key: str, out: Path) -> Path:
    """Тон трека 50 с: частота 220–880 Гц и вибрато из хэша ключа (AAC в m4a)."""
    s = _seed(key)
    freq = 220 + int.from_bytes(s[:2], "big") % 660
    vibrato = 2 + s[2] % 6
    expr = f"sin(2*PI*t*({freq}+8*sin(2*PI*{vibrato}*t)))*0.5*(1-exp(-4*t))"
    return _ffmpeg_audio(expr, TONE_SECONDS, out / f"{_slug(key)}.m4a")


def soundtrack(key: str, out: Path) -> Path:
    """«Саундтрек» фильма 50 с: аккорд из двух тонов с медленной пульсацией."""
    s = _seed(f"{key}#soundtrack")
    low = 110 + s[0] % 110
    high = low * (1.5 if s[1] % 2 else 1.25)
    pulse = 1 + s[2] % 3
    expr = f"(sin(2*PI*{low}*t)+0.6*sin(2*PI*{high}*t))*0.3*(0.6+0.4*sin(2*PI*{pulse}*t/4))"
    return _ffmpeg_audio(expr, FILM_AUDIO_SECONDS, out / f"soundtrack-{_short(key)}.m4a")


def clip(key: str, out: Path) -> Path:
    """«Звук фильма» 50 с: свип частоты с прерывистой огибающей (без речи и музыки)."""
    s = _seed(f"{key}#clip")
    start = 300 + s[0] % 300
    span = 200 + s[1] % 400
    beat = 2 + s[2] % 4
    # Запятая в выражении экранируется: в lavfi она разделяет фильтры.
    gate = f"gt(sin(2*PI*{beat}*t)\\,-0.3)"
    expr = f"sin(2*PI*t*({start}+{span}*t/{FILM_AUDIO_SECONDS}))*0.4*{gate}"
    return _ffmpeg_audio(expr, FILM_AUDIO_SECONDS, out / f"clip-{_short(key)}.m4a")


def frame(key: str, out: Path, number: int | None = None) -> Path:
    """Абстрактный кадр 1280×720: градиент и фигуры (JPEG).

    Args:
        number: Номер кадра ``frame_sequence`` (1..6) — свой рисунок и имя
            ``<slug>-<n>.jpg``; ``None`` — основной кадр этапа 2 ``<slug>.jpg``.
    """
    name = _slug(key) if number is None else f"{_slug(key)}-{number}"
    path = out / f"{name}.jpg"
    if path.exists():
        return path
    s = _seed(key if number is None else f"{key}#frame{number}")
    w, h = FRAME_SIZE
    image = Image.new("RGB", FRAME_SIZE)
    draw = ImageDraw.Draw(image)
    top, bottom = (s[0], s[1], s[2]), (s[3], s[4], s[5])
    for y in range(h):
        k = y / (h - 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom, strict=True)),
        )
    for i in range(6):
        b = s[6 + i * 4 : 10 + i * 4]
        x, y, r = b[0] * w // 256, b[1] * h // 256, 40 + b[2] % 160
        color = (b[3], 255 - b[3], (b[0] + b[1]) % 256)
        shape = draw.ellipse if i % 2 else draw.rectangle
        shape([x - r, y - r, x + r, y + r], fill=color)
    image.save(path, format="JPEG", quality=88)
    return path


def main() -> None:
    """Сгенерировать файлы для всех треков и фильмов из ``seeds/catalog/*.jsonl``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for row in _rows(args.catalog):
        key = str(row["key"])
        if row.get("kind") == "track":
            tone(key, args.out)
            continue
        frame(key, args.out)
        for number in range(1, FRAMES_PER_FILM + 1):
            frame(key, args.out, number)
        soundtrack(key, args.out)
        clip(key, args.out)


if __name__ == "__main__":
    main()
