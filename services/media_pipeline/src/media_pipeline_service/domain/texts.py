"""Тексты загадок ``description``/``emoji`` (ADR 0024): содержимое текстового исходника и
JSON-ассет уровня по ``contracts/clues/text_asset.v1.json``.

Содержимое текстового исходника собирается из события catalog и **не содержит названия**
единицы: только одобренные модератором тексты (сюжет с маскировкой, слоган, эмодзи) и
метаданные, которые открываются на уровнях (год, жанры, страны, режиссёры, актёры).
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from media_pipeline_service.domain.clues import DescriptionField, DescriptionParams, EmojiParams
from media_pipeline_service.domain.params import canonical_json

TEXT_CONTENT_VERSION = 1
TEXT_ASSET_VERSION = 1
TEXT_ASSET_MIME = "application/json"
TEXT_ASSET_EXT = "json"

# Пределы text_asset.v1 (contracts/clues/text_asset.v1.json).
_MAX_TEXT = 2000
_MAX_VALUE = 500
_MAX_CAST = 3

# Поле уровня description → ключ содержимого текстового исходника.
_CONTENT_KEY: dict[DescriptionField, str] = {
    "plot_masked": "plot_masked",
    "tagline": "tagline",
    "year": "year",
    "genres": "genres",
    "country": "countries",
    "director": "directors",
    "cast_top3": "cast",
}
# Поля, которые показываются отдельным блоком текста (остальные — строками блока fields).
_TEXT_BLOCKS: dict[DescriptionField, str] = {"plot_masked": "plot", "tagline": "tagline"}


# ---------------------------------------------------------------- графемы
_REGIONAL = range(0x1F1E6, 0x1F200)
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_TAGS = range(0xE0020, 0xE0080)
_VARIATION = range(0xFE00, 0xFE10)
_ZWJ = 0x200D
_KEYCAP = 0x20E3


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
            cp = ord(text[i])
            if cp == _ZWJ and i + 1 < n:
                i += 2
                continue
            if _extends(text[i]):
                i += 1
                continue
            break
        result.append(text[start:i])
    return result


# ---------------------------------------------------------------- содержимое исходника
def text_content(
    *,
    clue_texts: Mapping[str, str],
    year: int | None,
    genres: Sequence[str],
    countries: Sequence[str],
    directors: Sequence[str],
    cast: Sequence[str],
) -> dict[str, Any]:
    """Содержимое текстового исходника единицы (без названия).

    Args:
        clue_texts: Одобренные тексты (``plot_masked``, ``tagline``, ``emoji``).
        cast: Актёры по порядку в титрах (берутся первые три).
    """
    content: dict[str, Any] = {"version": TEXT_CONTENT_VERSION}
    for key in ("plot_masked", "tagline", "emoji"):
        value = clue_texts.get(key)
        if value and value.strip():
            content[key] = value.strip()
    if year is not None:
        content["year"] = year
    for key, values in (
        ("genres", genres),
        ("countries", countries),
        ("directors", directors),
        ("cast", list(cast)[:_MAX_CAST]),
    ):
        cleaned = [v.strip() for v in values if v and v.strip()]
        if cleaned:
            content[key] = cleaned
    return content


def has_text_facts(content: Mapping[str, Any]) -> bool:
    """В содержимом есть что показать (не только версия формата)."""
    return any(key != "version" for key in content)


# Пространство имён uuid5 текстовых исходников: id = f(единица, хэш содержимого).
_TEXT_SOURCE_NAMESPACE = UUID("0192a3b4-c5d6-7e8f-9a0b-7e4700000000")


def text_source_identity(media_item_id: UUID, content: Mapping[str, Any]) -> tuple[UUID, str, int]:
    """id, sha256 и размер текстового исходника.

    id зависит от хэша содержимого: изменение текста или метаданных даёт новый исходник, а
    наборы, построенные на старом, перепланируются в новое поколение (как при смене кадра).
    """
    data = canonical_json(content).encode()
    digest = hashlib.sha256(data).hexdigest()
    source_id = uuid.uuid5(_TEXT_SOURCE_NAMESPACE, f"{media_item_id}:{digest}")
    return source_id, digest, len(data)


def _field_value(content: Mapping[str, Any], field: DescriptionField) -> str | None:
    value = content.get(_CONTENT_KEY[field])
    if value in (None, "", []):
        return None
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def description_supported(content: Mapping[str, Any], params: DescriptionParams) -> bool:
    """У единицы есть все поля, которые открывают уровни профиля."""
    return all(_field_value(content, f) is not None for f in params.all_fields)


def _text_asset(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": TEXT_ASSET_VERSION, "blocks": blocks}


def description_levels(
    content: Mapping[str, Any], params: DescriptionParams
) -> list[dict[str, Any]]:
    """JSON-ассеты уровней ``description``: каждый уровень показывает все открытые поля.

    Сюжет и слоган — отдельные блоки ``plot``/``tagline``; остальные поля — строки одного
    блока ``fields`` (он стоит там, где открылось первое такое поле).

    Raises:
        ValueError: нет поля, которое открывает уровень (см. ``description_supported``).
    """
    opened: list[DescriptionField] = []
    levels: list[dict[str, Any]] = []
    for level in params.levels:
        opened.extend(level.fields)
        blocks: list[dict[str, Any]] = []
        items: list[dict[str, str]] = []
        for field in opened:
            value = _field_value(content, field)
            if value is None:
                raise ValueError(f"description: у единицы нет поля {field}")
            kind = _TEXT_BLOCKS.get(field)
            if kind is not None:
                blocks.append({"kind": kind, "text": value[:_MAX_TEXT]})
                continue
            if not items:
                blocks.append({"kind": "fields", "items": items})
            items.append({"field": field, "value": value[:_MAX_VALUE]})
        levels.append(_text_asset(blocks))
    return levels


def emoji_supported(content: Mapping[str, Any], params: EmojiParams) -> bool:
    """Эмодзи одобрены и их не меньше, чем открывает самый «щедрый» явный уровень."""
    emoji = content.get("emoji")
    return isinstance(emoji, str) and len(graphemes(emoji)) >= max(params.max_count, 1)


def emoji_levels(content: Mapping[str, Any], params: EmojiParams) -> list[dict[str, Any]]:
    """JSON-ассеты уровней ``emoji``: первые ``count`` графем (``None`` — все).

    Raises:
        ValueError: эмодзи нет.
    """
    emoji = content.get("emoji")
    if not isinstance(emoji, str) or not emoji:
        raise ValueError("emoji: у единицы нет одобренных эмодзи")
    parts = graphemes(emoji)
    return [
        _text_asset([{"kind": "emoji", "text": "".join(parts[: level.count])}])
        for level in params.levels
    ]
