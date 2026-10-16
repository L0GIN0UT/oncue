"""Текст кураторского сборника в YAML-редакторе (D14): разбор, вывод, ошибки с номерами строк.

Чистые функции без Django (их проверяют unit-тесты). Разбор YAML и номера строк по пути —
из ``game_config.config_text`` (повторяющиеся ключи, якоря и даты там уже запрещены).
Форма тела — как ``CuratedCollectionIn`` в contracts/openapi/collections.json; единица —
``media_item_id`` строкой или объектом ``{media_item_id, custom: {emoji}}``. Проверки по
каталогу, словарям тегов и обложек и строгие эмодзи делает collections: здесь — только то,
без чего запрос не по контракту (поля, типы, UUID).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import yaml

from admin_service.apps.game_config import config_text
from admin_service.apps.game_config.config_text import ConfigTextError

# Порядок полей в редакторе — как в CuratedCollectionIn (reason вводится полем формы).
FIELDS = (
    "domain",
    "title",
    "description",
    "tags",
    "cover",
    "default_mode_key",
    "visibility",
    "items",
)
_ITEM_FIELDS = frozenset({"media_item_id", "custom"})
_CUSTOM_FIELDS = frozenset({"emoji"})
_COMMENT_UNSAFE = re.compile(r"[\x00-\x1f\x7f  ]+")

# Коды ошибок публикации collections (``details.errors[].code``) — человеческим текстом.
_ERROR_TEXTS: Mapping[str, str] = {
    "items_too_few": "единиц меньше {min}",
    "items_too_many": "единиц больше {max}",
    "item_duplicate": "единица повторяется",
    "item_unavailable": "единицы нет в каталоге или она заблокирована",
    "item_domain_mismatch": "единица из другого домена",
    "mode_invalid": "режим должен быть вида <домен>.collection.<имя>",
    "emoji_invalid": "только эмодзи (RGI), без текста и пробелов, не слишком много",
    "emoji_too_short": "эмодзи меньше {min}",
    "tag_unknown": "неизвестный тег или тег другого домена",
    "tags_too_many": "тегов больше {max}",
    "cover_unknown": "неизвестный пресет обложки",
    "title_invalid": "название не подходит: {reason}",
    "description_invalid": "описание не подходит: {reason}",
}
# Причины ``gv_text.ugc`` (``details.errors[].reason``).
_UGC_TEXTS: Mapping[str, str] = {
    "ugc.empty": "пусто",
    "ugc.too_long": "слишком длинно",
    "ugc.denied_word": "запрещённое слово",
    "ugc.link_forbidden": "ссылки запрещены",
    "ugc.mention_forbidden": "@-упоминания запрещены",
    "ugc.email_forbidden": "e-mail запрещён",
}


def _fail(text: str, path: str, message: str) -> ConfigTextError:
    return ConfigTextError(f"{path}: {message}", config_text.line_of(text, path))


def _item(text: str, index: int, raw: Any) -> dict[str, Any]:
    path = f"items[{index}]"
    item: dict[str, Any] = {"media_item_id": raw} if isinstance(raw, str) else raw
    if not isinstance(item, dict):
        raise _fail(text, path, "единица — id строкой или объект {media_item_id, custom}")
    unknown = sorted(set(item) - _ITEM_FIELDS)
    if unknown:
        raise _fail(text, f"{path}.{unknown[0]}", "неизвестное поле единицы")
    raw_id = item.get("media_item_id")
    try:
        media_item_id = UUID(raw_id) if isinstance(raw_id, str) else None
    except ValueError:
        media_item_id = None
    if media_item_id is None:
        raise _fail(text, f"{path}.media_item_id", "нужен media_item_id (UUID)")
    item = {**item, "media_item_id": str(media_item_id)}  # каноничная запись UUID
    custom = item.get("custom")
    if custom is not None:
        if not isinstance(custom, dict) or set(custom) - _CUSTOM_FIELDS:
            raise _fail(text, f"{path}.custom", "custom — объект {emoji: ...}")
        if custom.get("emoji") is not None and not isinstance(custom["emoji"], str):
            raise _fail(text, f"{path}.custom.emoji", "эмодзи — строка")
    return item


def parse(text: str) -> dict[str, Any]:
    """Текст редактора → тело сборника (поля ``CuratedCollectionIn`` без ``reason``).

    Raises:
        ConfigTextError: синтаксис YAML, неизвестное поле, единица не по форме или id не UUID.
    """
    body = config_text.parse(text, "yaml")
    for key in body:
        if key not in FIELDS:
            hint = " (причина вводится полем формы)" if key == "reason" else ""
            raise _fail(text, key, f"неизвестное поле{hint}")
    items = body.get("items")
    if not isinstance(items, list):
        raise _fail(text, "items", "нужен список единиц")
    body["items"] = [_item(text, i, raw) for i, raw in enumerate(items)]
    return body


def body_of(detail: Mapping[str, Any]) -> dict[str, Any]:
    """Тело сборника из карточки admin-API (для редактора и diff при конфликте версий)."""
    body: dict[str, Any] = {k: detail.get(k) for k in FIELDS if k != "items"}
    if body.get("description") is None:
        body.pop("description")
    if not isinstance(body.get("cover"), dict):
        body.pop("cover")
    else:
        body["cover"] = {"preset": body["cover"].get("preset")}
    items: list[dict[str, Any]] = []
    for row in sorted(_rows(detail.get("items")), key=lambda r: _position(r.get("position"))):
        item: dict[str, Any] = {"media_item_id": str(row.get("media_item_id"))}
        custom = row.get("custom")
        emoji = custom.get("emoji") if isinstance(custom, dict) else None
        if isinstance(emoji, str) and emoji:
            item["custom"] = {"emoji": emoji}
        items.append(item)
    body["items"] = items
    return body


def _rows(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _position(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _comment(row: Mapping[str, Any]) -> str:
    """Подпись единицы комментарием YAML: название и год (переводы строк вырезаются)."""
    title = _COMMENT_UNSAFE.sub(" ", str(row.get("title") or "")).strip()
    year = row.get("year")
    label = f"{title} ({year})" if isinstance(year, int) and title else title
    return f"  # {label}" if label else ""


def dump(detail: Mapping[str, Any]) -> str:
    """Карточка admin-API → текст редактора: поля сборника и единицы с подписями-комментариями.

    Комментарий (название и год) помогает узнать единицу по id; при разборе он отбрасывается.
    """
    body = body_of(detail)
    items = body.pop("items")
    head = yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100)
    if not items:
        return f"{head}items: []\n"
    titles = {str(r.get("media_item_id")): r for r in _rows(detail.get("items"))}
    lines = [head.rstrip("\n"), "items:"]
    for item in items:
        comment = _comment(titles.get(item["media_item_id"], {}))
        emoji = item.get("custom", {}).get("emoji")
        if emoji is None:
            lines.append(f"  - {item['media_item_id']}{comment}")
        else:
            # JSON-строка — корректный скаляр YAML в двойных кавычках (эмодзи без экранирования).
            quoted = json.dumps(emoji, ensure_ascii=False)
            lines.append(f"  - media_item_id: {item['media_item_id']}{comment}")
            lines.append(f"    custom: {{emoji: {quoted}}}")
    return "\n".join(lines) + "\n"


def template(domain: str) -> str:
    """Заготовка нового кураторского сборника домена ``domain``.

    Режим по умолчанию не подставляется: шаблоны ``<домен>.collection.*`` задаёт game, а
    выбрать нужный должен куратор (collections проверит формат — ``mode_invalid``).
    """
    body = {
        "domain": domain,
        "title": "",
        "description": "",
        "tags": [],
        "visibility": "public",
    }
    head = yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100)
    return (
        f"{head}"
        f"default_mode_key: ''  # шаблон режима game: {domain}.collection.<имя>\n"
        "items:\n"
        "  # единица — media_item_id из каталога; свои эмодзи:\n"
        "  # - media_item_id: <uuid>\n"
        '  #   custom: {emoji: "🚢🧊💔🎻🌊🚪"}\n'
    )


def _service_path(loc: Any) -> str:
    """``loc`` ошибки валидации FastAPI (``["body", "items", 0, "media_item_id"]``) → путь."""
    parts = list(loc) if isinstance(loc, list) else []
    if parts and parts[0] == "body":
        parts = parts[1:]
    path = ""
    for part in parts:
        if isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path


def _message(row: Mapping[str, Any]) -> str:
    code = str(row.get("code") or "")
    reason = str(row.get("reason") or "")
    values = {
        "min": row.get("min", "?"),
        "max": row.get("max", "?"),
        "reason": _UGC_TEXTS.get(reason, reason or "?"),
    }
    template_text = _ERROR_TEXTS.get(code)
    return template_text.format(**values) if template_text else code


def error_rows(
    text: str, *, details: Mapping[str, Any], errors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ошибки сервиса с путями → строки для шаблона: путь, понятный текст, номер строки.

    ``details.errors`` — ошибки публикации collections (``[{path, code, min?, max?,
    reason?}]``); ``errors`` — ошибки валидации запроса FastAPI (``[{loc, msg}]``).
    """
    rows: list[dict[str, Any]] = []
    for item in _rows(details.get("errors")):
        path = str(item.get("path") or "")
        rows.append(
            {
                "path": path or "(корень)",
                "code": str(item.get("code") or ""),
                "message": _message(item),
                "line": config_text.line_of(text, path),
            }
        )
    for item in errors:
        path = _service_path(item.get("loc"))
        rows.append(
            {
                "path": path or "(корень)",
                "code": str(item.get("type") or ""),
                "message": str(item.get("msg") or ""),
                "line": config_text.line_of(text, path),
            }
        )
    return rows
