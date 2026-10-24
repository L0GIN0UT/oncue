"""Картинки сидовых предметов магазина: WebP (статика) и animated WebP.

Детерминированно: никакой случайности, цвета берутся из таблицы редкостей, узор - из слота,
оттенок - из хеша ключа. Результат коммитится (``seeds/progression/items/images/<key>.webp``),
каждый файл <= 50 КБ и укладывается в ``shop.media`` из ``seeds/progression/config.yaml``.

Запуск::

    uv run python seeds/progression/generate_items.py [--out seeds/progression/items/images]

Нужен Pillow. Прав третьих лиц нет (ADR 0014): геометрические фигуры.
"""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path

import yaml
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ITEMS_FILE = HERE / "items" / "items.yaml"
DEFAULT_OUT = HERE / "items" / "images"
SIZE = 128
BACKGROUND_SIZE = (256, 144)
FRAMES = 8
FRAME_MS = 100
# Основной и акцентный цвета по редкости:
# common - серо-голубой, rare - синий, epic - золото+маджента.
PALETTE = {
    "common": ((96, 112, 132), (176, 190, 208)),
    "rare": ((40, 96, 220), (120, 220, 255)),
    "epic": ((210, 150, 20), (255, 70, 170)),
}


def _shade(base: tuple[int, int, int], key: str) -> tuple[int, int, int]:
    """Лёгкий сдвиг цвета по хешу ключа: предметы одной редкости отличаются друг от друга."""
    digest = hashlib.sha256(key.encode()).digest()
    return tuple(max(0, min(255, c + digest[i] % 41 - 20)) for i, c in enumerate(base))  # type: ignore[return-value]


def _size(slot: str) -> tuple[int, int]:
    return BACKGROUND_SIZE if slot == "background" else (SIZE, SIZE)


def _gradient(
    size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]
) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size, top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)  # type: ignore[arg-type]
    return image


def render(key: str, slot: str, rarity: str, phase: float) -> Image.Image:
    """Кадр предмета; ``phase`` в [0, 1) двигает узор (у статики - 0)."""
    main, accent = PALETTE[rarity]
    main, accent = _shade(main, key), _shade(accent, key + "!")
    size = _size(slot)
    width, height = size
    image = _gradient(size, main, tuple(c // 3 for c in main))  # type: ignore[arg-type]
    draw = ImageDraw.Draw(image)
    shift = round(phase * 2 * math.pi * 100) / 100
    if slot == "avatar":
        draw.ellipse([14, 14, width - 14, height - 14], fill=accent)
        dx = round(math.sin(shift) * 6)
        draw.ellipse([44 + dx, 44, 84 + dx, 84], fill=main)
        n = 3 + {"common": 0, "rare": 1, "epic": 2}[rarity]
        for i in range(n):
            x = 24 + i * (80 // max(1, n - 1))
            draw.rectangle([x - 3, 94, x + 3, 108], fill=main)
    elif slot == "frame":
        draw.rectangle([0, 0, width - 1, height - 1], outline=accent, width=10)
        inner = 14
        draw.rectangle([inner, inner, width - 1 - inner, height - 1 - inner], outline=main, width=4)
        corner = round(18 + math.sin(shift) * 6)
        for cx, cy in ((0, 0), (width, 0), (0, height), (width, height)):
            draw.ellipse([cx - corner, cy - corner, cx + corner, cy + corner], fill=accent)
    elif slot == "title":
        for i in range(6):
            y = 8 + i * 20
            draw.rectangle([0, y, width, y + 8], fill=accent if i % 2 == 0 else main)
        draw.polygon([(14, 64), (width - 14, 40), (width - 14, 88)], fill=accent)
    else:  # background
        for i in range(8):
            x = round(((i * 37 + phase * 256) % (width + 40)) - 20)
            y = 10 + (i * 53) % (height - 30)
            r = 8 + (i * 7) % 14
            draw.ellipse([x - r, y - r, x + r, y + r], fill=accent)
        draw.rectangle([0, height - 24, width, height], fill=main)
    return image


def save_item(out: Path, key: str, slot: str, rarity: str, media_kind: str) -> Path:
    """Записать ``<key>.webp``: статика - один кадр, animated - ``FRAMES`` кадров."""
    path = out / f"{key}.webp"
    if media_kind == "animated":
        frames = [render(key, slot, rarity, i / FRAMES) for i in range(FRAMES)]
        frames[0].save(
            path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=FRAME_MS,
            loop=0,
            quality=60,
            method=6,
        )
    else:
        render(key, slot, rarity, 0).save(path, format="WEBP", quality=70, method=6)
    return path


def generate(out: Path = DEFAULT_OUT, items_file: Path = ITEMS_FILE) -> list[Path]:
    """Сгенерировать картинки всех предметов из ``items.yaml``."""
    spec = yaml.safe_load(items_file.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    return [
        save_item(out, i["key"], i["slot"], i["rarity"], i["media_kind"]) for i in spec["items"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    for path in generate(args.out):
        print(f"{path.name}: {path.stat().st_size} байт")  # noqa: T201 - CLI-скрипт


if __name__ == "__main__":
    main()
