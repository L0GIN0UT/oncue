"""Синтетические исходники ассетов для local/e2e: тоны для треков и абстрактные кадры для фильмов.

Никаких записей и кадров третьих лиц (ADR 0014): каждый трек — синус своей частоты с
огибающей, каждый кадр — градиент с геометрическими фигурами, цвета выводятся из ключа
единицы каталога. Результат детерминирован и пересобирается в любой момент, поэтому
бинарные файлы в git не хранятся.

Запуск::

    uv run python seeds/media/generate.py [--out seeds/media/out]

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
TONE_SECONDS = 20
FRAME_SIZE = (1280, 720)


def _rows(name: str) -> list[dict[str, object]]:
    lines = (CATALOG / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip() and not line.startswith("#")]


def _slug(key: str) -> str:
    return key.replace(":", "-")


def _seed(key: str) -> bytes:
    return hashlib.sha256(key.encode()).digest()


def tone(key: str, out: Path) -> Path:
    """Тон 20 с: частота 220–880 Гц и вибрато из хэша ключа (AAC в m4a)."""
    s = _seed(key)
    freq = 220 + int.from_bytes(s[:2], "big") % 660
    vibrato = 2 + s[2] % 6
    path = out / f"{_slug(key)}.m4a"
    expr = f"sin(2*PI*t*({freq}+8*sin(2*PI*{vibrato}*t)))*0.5*(1-exp(-4*t))"
    subprocess.run(  # noqa: S603 — аргументы формируются здесь же, без ввода пользователя
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi",  # noqa: S607
         "-i", f"aevalsrc={expr}:s=44100:d={TONE_SECONDS}",
         "-c:a", "aac", "-b:a", "96k", str(path)],
        check=True,
    )  # fmt: skip
    return path


def frame(key: str, out: Path) -> Path:
    """Абстрактный кадр 1280×720: градиент и фигуры (JPEG)."""
    s = _seed(key)
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
    path = out / f"{_slug(key)}.jpg"
    image.save(path, format="JPEG", quality=88)
    return path


def main() -> None:
    """Сгенерировать файлы для всех треков и фильмов из ``seeds/catalog``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=HERE / "out")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for row in _rows("tracks.jsonl"):
        tone(str(row["key"]), args.out)
    for row in _rows("films.jsonl"):
        frame(str(row["key"]), args.out)


if __name__ == "__main__":
    main()
