"""Графемы и строгая проверка эмодзи (план этапа 3b §2.7, ADR 0029–0030).

``graphemes`` — тот же упрощённый алгоритм расширенных кластеров UAX #29, что у catalog
(``catalog_service.domain.clue_texts``) и media-pipeline (``media_pipeline_service.domain.
texts``): лимит «≤ 12 эмодзи» в collections и «первые N эмодзи» уровня в media-pipeline
считаются одинаково (общие golden-векторы в тестах всех трёх).

``is_strict_emoji`` пропускает только последовательности из таблицы RGI Unicode Emoji 16.0
(``data/emoji-sequences.txt``, лицензия Unicode — ``data/LICENSE-UNICODE.txt``): никаких
букв, цифр без keycap, пробелов, «голых» ZWJ и непарных региональных индикаторов. Таблица —
файл данных, а не ``unicodedata``: в Python 3.13 база Unicode 15.1, и эмодзи 16.0 там
не назначены.
"""

from __future__ import annotations

import unicodedata
from functools import cache
from importlib.resources import files

_REGIONAL = range(0x1F1E6, 0x1F200)
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_TAGS = range(0xE0020, 0xE0080)
_VARIATION = range(0xFE00, 0xFE10)
_ZWJ = 0x200D
_KEYCAP = 0x20E3

EMOJI_DATA_FILE = "emoji-sequences.txt"
"""Таблица RGI в каталоге ``data`` пакета ``gv_text``."""


def _extends(ch: str) -> bool:
    cp = ord(ch)
    return (
        unicodedata.category(ch) in ("Mn", "Me", "Mc")
        or cp in _VARIATION
        or cp in _SKIN_TONES
        or cp in _TAGS
        or cp == _KEYCAP
    )


def graphemes(text: str) -> list[str]:
    """Разбить строку на графемы (упрощённые расширенные кластеры UAX #29).

    Достаточно для эмодзи: флаги (пары региональных индикаторов), модификаторы тона кожи,
    вариационные селекторы, keycap, теги и последовательности через ZWJ — одна графема.
    Склейка строки из графем всегда даёт исходную строку.
    """
    result: list[str] = []
    i, n = 0, len(text)
    while i < n:
        start = i
        first = text[i]
        i += 1
        if ord(first) in _REGIONAL and i < n and ord(text[i]) in _REGIONAL:
            i += 1
        while i < n:
            if ord(text[i]) == _ZWJ and i + 1 < n:
                i += 2
                continue
            if _extends(text[i]):
                i += 1
                continue
            break
        result.append(text[start:i])
    return result


def _parse(table: str) -> frozenset[str]:
    sequences: set[str] = set()
    for raw in table.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        points, _, _kind = line.partition(";")
        sequences.add("".join(chr(int(cp, 16)) for cp in points.split()))
    return frozenset(sequences)


@cache
def rgi_emoji() -> frozenset[str]:
    """Все последовательности RGI из таблицы пакета (загружаются один раз)."""
    table = files("gv_text").joinpath("data", EMOJI_DATA_FILE).read_text(encoding="utf-8")
    return _parse(table)


def is_strict_emoji(text: str, *, max_graphemes: int | None = None) -> bool:
    """Строка — только RGI-эмодзи подряд, без пробелов и прочих символов.

    Args:
        text: Проверяемая строка (без нормализации: ``FE0F`` и ZWJ значимы).
        max_graphemes: Не больше стольких графем (``None`` — без ограничения).

    Returns:
        ``True``, если строка непустая, каждая её графема — последовательность RGI и
        графем не больше ``max_graphemes``.
    """
    if not text:
        return False
    parts = graphemes(text)
    if max_graphemes is not None and len(parts) > max_graphemes:
        return False
    table = rgi_emoji()
    return all(part in table for part in parts)
