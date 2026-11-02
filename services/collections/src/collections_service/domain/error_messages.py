"""Тексты нарушений черновика и публикации (ru) по коду, пути и параметрам.

Нужны, чтобы 422 при публикации объяснял причину (в ``details.errors[].message``), а не
оставлял игрока гадать. Коды остаются стабильным контрактом для клиента; тексты — подсказка.
"""

from __future__ import annotations

import re
from typing import Any

_INDEX_RE = re.compile(r"items\[(\d+)\]")

_UGC_REASONS = {
    "ugc.empty": "не должно быть пустым",
    "ugc.too_long": "слишком длинное",
    "ugc.denied_word": "содержит недопустимое слово",
    "ugc.link_forbidden": "содержит ссылку",
    "ugc.mention_forbidden": "содержит упоминание (@)",
    "ugc.email_forbidden": "содержит адрес почты",
}

_FIELDS = {"title": "Название", "description": "Описание"}

_TEMPLATES = {
    "collections.items_too_few": "Нужно минимум {min} единиц, сейчас {actual}",
    "collections.items_too_many": "Слишком много единиц: максимум {max}",
    "collections.item_duplicate": "{item} повторяется",
    "collections.item_domain_mismatch": "{item} из другого раздела, чем сборник",
    "collections.item_unavailable": "{item} недоступна: её нет в каталоге или она скрыта",
    "collections.mode_invalid": "Выберите режим игры для сборника",
    "collections.emoji_invalid": "{item}: эмодзи не подходит",
    "collections.emoji_too_short": "{item}: эмодзи короче {min} символов",
    "collections.tag_unknown": "Неизвестный или неподходящий тег",
    "collections.tags_too_many": "Слишком много тегов: максимум {max}",
    "collections.cover_unknown": "Неизвестная обложка",
}


def _item(path: str) -> str:
    match = _INDEX_RE.match(path)
    return f"Единица №{int(match.group(1)) + 1}" if match else "Единица"


def error_message(path: str, code: str, extra: dict[str, Any]) -> str:
    """Человекочитаемый текст нарушения."""
    if code in ("collections.title_invalid", "collections.description_invalid"):
        field = _FIELDS["title" if code.endswith("title_invalid") else "description"]
        reason = _UGC_REASONS.get(str(extra.get("reason")), "не подходит")
        return f"{field}: {reason}"
    template = _TEMPLATES.get(code)
    if template is None:
        return "Сборник не проходит проверку"
    return template.format(item=_item(path), **extra)
