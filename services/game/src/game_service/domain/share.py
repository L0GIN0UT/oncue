"""Текст «Поделиться» (D14): сетка попыток как в Wordle, без ответов.

Строка на раунд: клетка на каждый открытый уровень. Названий и идентификаторов в тексте
нет — им можно делиться до того, как друзья сыграли дейлик.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from gv_game_kernel import GuessKind, MatchVerdict, RoundResult, RoundState

CELL_MISS = "⬛"  # чёрный квадрат: промах
CELL_SKIP = "⬜"  # белый квадрат: пропуск
CELL_PARTIAL = "\U0001f7e8"  # жёлтый квадрат: частичный ответ
CELL_EXACT = "\U0001f7e9"  # зелёный квадрат: угадано
CELL_VOID = "▫"  # маленький квадрат: раунд аннулирован
CELL_GAVE_UP = "\U0001f7e5"  # красный квадрат: сдался

_BY_VERDICT = {
    MatchVerdict.EXACT: CELL_EXACT,
    MatchVerdict.PARTIAL: CELL_PARTIAL,
    MatchVerdict.MISS: CELL_MISS,
}


def round_cells(state: RoundState) -> str:
    """Клетки одного раунда."""
    if state.outcome is RoundResult.VOID:
        return CELL_VOID
    cells = [
        CELL_SKIP if g.kind is GuessKind.SKIP or g.verdict is None else _BY_VERDICT[g.verdict]
        for g in state.guesses
    ]
    if state.outcome is RoundResult.GAVE_UP:
        cells.append(CELL_GAVE_UP)
    return "".join(cells)


def share_text(
    *,
    title: str,
    mode_key: str,
    day: date | None,
    score: int,
    max_score: int,
    rounds: Sequence[RoundState],
    url: str = "",
) -> str:
    """Текст-сетка итога сессии."""
    header = f"{title} {mode_key}" + (f" {day.isoformat()}" if day else "")
    lines = [header, f"{score}/{max_score}", *(round_cells(r) for r in rounds)]
    if url:
        lines.append(url)
    return "\n".join(lines)
