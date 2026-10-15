"""Встроенные форматы ответа (ADR 0023).

``classic`` — ввод названия (выбор из автокомплита или свободный текст). Формат ``pick``
(выбор варианта раунда) появляется вместе с источниками пары и сетки.
"""

from __future__ import annotations

from typing import ClassVar

from gv_game_kernel.builtin.clues import CLASSIC_FORMAT


class ClassicFormat:
    """Классический ввод: единица из автокомплита или свободный текст."""

    key: ClassVar[str] = CLASSIC_FORMAT
    input_kinds: ClassVar[frozenset[str]] = frozenset({"item", "text"})
    needs_options: ClassVar[bool] = False
