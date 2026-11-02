r"""JSONL без сюрпризов юникода.

``str.splitlines()`` режет текст не только по ``\n``, но и по U+2028, U+2029, U+0085 и другим
разделителям, а ``json.dumps(ensure_ascii=False)`` выводит их в строке как есть. Описание фильма
с таким символом ломало и клиент засева, и catalog. Читаем только по ``\n``, а при записи
экранируем эти символы (JSON допускает ``\uXXXX`` в строке).
"""

from __future__ import annotations

import json
from typing import Any

# Символы, которые splitlines() и часть читалок считают концом строки, кроме самого «\n».
_TABLE = str.maketrans(
    {
        "\u0085": "\\u0085",
        " ": "\\u2028",
        " ": "\\u2029",
    }
)


def split_lines(text: str) -> list[str]:
    r"""Строки текста, разделённые только ``\n`` (``\r\n`` допустим); без хвостовой пустой."""
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def dumps_line(row: Any) -> str:
    """Строка JSONL: кириллица как есть, U+2028, U+2029 и U+0085 экранированы."""
    return json.dumps(row, ensure_ascii=False).translate(_TABLE)
