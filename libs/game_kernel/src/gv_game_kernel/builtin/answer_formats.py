"""Встроенные форматы ответа (ADR 0023).

* ``classic`` — ввод названия (выбор из автокомплита или свободный текст);
* ``pick`` — выбор непрозрачного ``option_id`` из вариантов раунда (пара, сетка). Варианты
  не меняются от уровня к уровню, поэтому ответ в раунде один (``single_answer``).
"""

from __future__ import annotations

from typing import ClassVar

from gv_game_kernel.builtin.clues import CLASSIC_FORMAT, PICK_FORMAT


class ClassicFormat:
    """Классический ввод: единица из автокомплита или свободный текст."""

    key: ClassVar[str] = CLASSIC_FORMAT
    input_kinds: ClassVar[frozenset[str]] = frozenset({"item", "text"})
    needs_options: ClassVar[bool] = False
    single_answer: ClassVar[bool] = False


class PickFormat:
    """Выбор варианта раунда: ответ ``{option_id}``, варианты строит источник."""

    key: ClassVar[str] = PICK_FORMAT
    input_kinds: ClassVar[frozenset[str]] = frozenset({"option"})
    needs_options: ClassVar[bool] = True
    single_answer: ClassVar[bool] = True
