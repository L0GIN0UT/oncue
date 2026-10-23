"""Тексты ladder-а в редакторе: пути ошибок rating (JSON Pointer) → строки текста.

Конфиг разбирается и печатается так же, как у режимов (``game_config.config_text``);
здесь только перевод путей: rating отдаёт ``/elo/start`` и ``/tiers/0/name``, а поиск строки
ждёт ``elo.start`` и ``tiers[0].name``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from admin_service.apps.game_config import config_text


def dotted(pointer: str) -> str:
    """``/tiers/0/name`` → ``tiers[0].name``; не JSON Pointer — как есть."""
    if not pointer.startswith("/"):
        return pointer
    out = ""
    for part in pointer[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")  # noqa: PLW2901
        out += f"[{part}]" if part.isdigit() else (f".{part}" if out else part)
    return out


def error_rows(
    text: str, *, details: Mapping[str, Any], errors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ошибки ``rating.ladder_invalid`` и валидации запроса → строки таблицы с номерами строк."""
    rows: list[dict[str, Any]] = []
    items = details.get("errors")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        path = dotted(str(item.get("path") or ""))
        rows.append(
            {
                "path": path or "(корень)",
                "code": str(item.get("code") or ""),
                "message": str(item.get("message") or item.get("msg") or item.get("code") or ""),
                "line": config_text.line_of(text, path),
            }
        )
    for item in errors:
        loc = item.get("loc")
        path = (
            ".".join(str(p) for p in loc if p not in ("body", "config"))
            if isinstance(loc, list)
            else ""
        )
        rows.append(
            {
                "path": path or "(корень)",
                "code": str(item.get("type") or ""),
                "message": str(item.get("msg") or ""),
                "line": config_text.line_of(text, path),
            }
        )
    return rows
